import boto3, os, re, logging, uuid, datetime, pytz, requests
from functools import wraps
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, ParseMode
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler

# --- Basic Configuration ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# --- AWS & Bot Configuration ---
REGION = os.getenv("AWS_REGION", "ap-south-1")
DYNAMODB_TABLE_NAME = "TelegramBotAuditLog"  # Table for Audit Logs
ADMIN_CHAT_ID = 8498983488  # Your Telegram user ID for reports

# --- AWS Clients ---
ssm = boto3.client("ssm", region_name=REGION)
ec2 = boto3.client("ec2", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
audit_log_table = dynamodb.Table(DYNAMODB_TABLE_NAME)

# --- Instance Price Map (for lightweight cost estimation) ---
# Prices are approximate hourly rates in USD for Linux instances in ap-south-1
INSTANCE_PRICE_MAP = {
    "t2.nano": 0.0062, "t2.micro": 0.012, "t2.small": 0.024,
    "t3.micro": 0.0112, "t3.small": 0.0224, "t3.medium": 0.0448,
}


# --- Load Token ---
def get_bot_token():
    try:
        resp = ssm.get_parameter(Name="/telegram/bot_token", WithDecryption=True)
        return resp["Parameter"]["Value"]
    except Exception as e:
        logger.error("Could not retrieve bot token: %s", e)
        exit()


TOKEN = get_bot_token()

# --- Auth & RBAC ---
ADMIN_USERS = [ADMIN_CHAT_ID]  # Full access
STANDARD_USERS = []  # Can list, start, stop
INSTANCE_RE = re.compile(r"^i-[0-9a-fA-F]{8,17}$")
EIP_ALLOC_RE = re.compile(r"^eipalloc-[0-9a-fA-F]{8,17}$")
SG_RE = re.compile(r"^sg-[0-9a-fA-F]{8,17}$")


def user_authorized(func):
    @wraps(func)
    def wrapped(update, context, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id in ADMIN_USERS or user_id in STANDARD_USERS:
            return func(update, context, *args, **kwargs)
        update.message.reply_text("Unauthorized.")
        return

    return wrapped


def admin_only(func):
    @wraps(func)
    def wrapped(update, context, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id in ADMIN_USERS:
            return func(update, context, *args, **kwargs)
        update.message.reply_text("Admin access required for this command.")
        return

    return wrapped


# --- Audit Logging ---
def log_action(user_id, command, details=""):
    try:
        audit_log_table.put_item(
            Item={
                "LogID": str(uuid.uuid4()),
                "Timestamp": str(datetime.datetime.now()),
                "UserID": str(user_id),
                "Command": command,
                "Details": details,
            }
        )
    except Exception as e:
        logger.error("Failed to write to audit log: %s", e)


# --- Command Handlers ---

@user_authorized
def start(update, context):
    log_action(update.effective_user.id, "/start")
    update.message.reply_text(
        "👋 Welcome to your AWS Manager Bot!\n\n"
        "Use /help to see all available commands."
    )


@user_authorized
def help_command(update, context):
    log_action(update.effective_user.id, "/help")
    commands = (
        "📌 *Available Commands:*\n\n"
        "`/list` – List all EC2 instances\n"
        "`/list <tag>` – Filter by `Environment` tag \(e\.g\., `/list dev`\)\n"
        "`/describe <id>` – Show instance details\n"
        "`/allocate_eip` – Allocate a new Elastic IP\n"
        "`/associate_eip <alloc_id> <inst_id>` – Associate EIP\n"
        "`/release_eip <alloc_id>` – Release an EIP\n"
        "`/add_ip <sg_id> <port>` – Add your IP to a Security Group\n"
        "`/remove_ip <sg_id> <port>` – Remove your IP from a SG\n"
        "`/cost` – Estimate current running costs\n"
    )
    update.message.reply_text(commands, parse_mode=ParseMode.MARKDOWN_V2)


@user_authorized
def list_instances(update, context):
    user_id = update.effective_user.id
    tag_filter = context.args[0] if context.args else None
    log_action(user_id, "/list", f"Tag: {tag_filter}")

    filters = [{"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
    if tag_filter:
        filters.append({"Name": "tag:Environment", "Values": [tag_filter]})

    resp = ec2.describe_instances(Filters=filters)
    instances_found = False
    for r in resp.get("Reservations", []):
        for i in r["Instances"]:
            instances_found = True
            iid = i["InstanceId"]
            state = i["State"]["Name"]
            name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "-")

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🟢 Start", callback_data=f"start:{iid}"),
                    InlineKeyboardButton("🔴 Stop", callback_data=f"stop:{iid}")
                ],
                [
                    InlineKeyboardButton("🔄 Reboot", callback_data=f"reboot:{iid}"),
                    InlineKeyboardButton("💥 Terminate", callback_data=f"terminate_confirm1:{iid}")
                ]
            ])
            msg = f"*{name}* (`{iid}`)\nState: `{state}`"
            update.message.reply_text(msg, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2)

    if not instances_found:
        update.message.reply_text(
            f"No instances found with tag '{tag_filter}'." if tag_filter else "No running or stopped instances found.")


@user_authorized
def describe_instance(update, context):
    user_id = update.effective_user.id
    if len(context.args) != 1 or not INSTANCE_RE.match(context.args[0]):
        return update.message.reply_text("Usage: `/describe <instance-id>`", parse_mode=ParseMode.MARKDOWN_V2)

    iid = context.args[0]
    log_action(user_id, "/describe", iid)
    try:
        resp = ec2.describe_instances(InstanceIds=[iid])
        i = resp["Reservations"][0]["Instances"][0]
        name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "-")
        details = (
            f"*Instance Details:*\n\n"
            f"📛 *Name:* {name}\n"
            f"🆔 *ID:* `{i['InstanceId']}`\n"
            f"📦 *Type:* `{i['InstanceType']}`\n"
            f"🌍 *AZ:* `{i['Placement']['AvailabilityZone']}`\n"
            f"⚡ *State:* `{i['State']['Name']}`\n"
            f"🌐 *Public IP:* `{i.get('PublicIpAddress', '-')}`\n"
            f"🔒 *Private IP:* `{i.get('PrivateIpAddress', '-')}`\n"
            f"🛡 *SGs:* `{', '.join([sg['GroupName'] for sg in i['SecurityGroups']])}`\n"
            f"⏱ *Launch Time:* `{i['LaunchTime'].strftime('%Y-%m-%d %H:%M')}`"
        )
        update.message.reply_text(details, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        update.message.reply_text(f"Error describing instance: {e}")


@admin_only
def allocate_eip(update, context):
    log_action(update.effective_user.id, "/allocate_eip")
    try:
        resp = ec2.allocate_address(Domain='vpc')
        ip = resp['PublicIp']
        alloc_id = resp['AllocationId']
        update.message.reply_text(f"✅ EIP Allocated\n*IP:* `{ip}`\n*Allocation ID:* `{alloc_id}`",
                                  parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        update.message.reply_text(f"Error: {e}")


@admin_only
def associate_eip(update, context):
    if len(context.args) != 2 or not EIP_ALLOC_RE.match(context.args[0]) or not INSTANCE_RE.match(context.args[1]):
        return update.message.reply_text("Usage: `/associate_eip <allocation-id> <instance-id>`",
                                         parse_mode=ParseMode.MARKDOWN_V2)

    alloc_id, iid = context.args
    log_action(update.effective_user.id, "/associate_eip", f"{alloc_id} -> {iid}")
    try:
        ec2.associate_address(AllocationId=alloc_id, InstanceId=iid)
        update.message.reply_text(f"✅ EIP `{alloc_id}` associated with instance `{iid}`",
                                  parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        update.message.reply_text(f"Error: {e}")


@admin_only
def release_eip(update, context):
    if len(context.args) != 1 or not EIP_ALLOC_RE.match(context.args[0]):
        return update.message.reply_text("Usage: `/release_eip <allocation-id>`", parse_mode=ParseMode.MARKDOWN_V2)

    alloc_id = context.args[0]
    log_action(update.effective_user.id, "/release_eip", alloc_id)
    try:
        ec2.release_address(AllocationId=alloc_id)
        update.message.reply_text(f"✅ EIP `{alloc_id}` released.", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        update.message.reply_text(f"Error: {e}")


@admin_only
def add_ip_to_sg(update, context):
    if len(context.args) != 2 or not SG_RE.match(context.args[0]):
        return update.message.reply_text("Usage: `/add_ip <sg-id> <port>`", parse_mode=ParseMode.MARKDOWN_V2)

    sg_id, port = context.args
    log_action(update.effective_user.id, "/add_ip", f"{sg_id}:{port}")
    try:
        ip = requests.get('https://api.ipify.org').text
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {'IpProtocol': 'tcp', 'FromPort': int(port), 'ToPort': int(port), 'IpRanges': [{'CidrIp': f'{ip}/32'}]}]
        )
        update.message.reply_text(f"✅ IP `{ip}` added to `{sg_id}` on port `{port}`", parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        update.message.reply_text(f"Error: {e}")


@admin_only
def remove_ip_from_sg(update, context):
    if len(context.args) != 2 or not SG_RE.match(context.args[0]):
        return update.message.reply_text("Usage: `/remove_ip <sg-id> <port>`", parse_mode=ParseMode.MARKDOWN_V2)

    sg_id, port = context.args
    log_action(update.effective_user.id, "/remove_ip", f"{sg_id}:{port}")
    try:
        ip = requests.get('https://api.ipify.org').text
        ec2.revoke_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                {'IpProtocol': 'tcp', 'FromPort': int(port), 'ToPort': int(port), 'IpRanges': [{'CidrIp': f'{ip}/32'}]}]
        )
        update.message.reply_text(f"✅ IP `{ip}` removed from `{sg_id}` on port `{port}`",
                                  parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        update.message.reply_text(f"Error: {e}")


@user_authorized
def cost_command(update, context):
    log_action(update.effective_user.id, "/cost")
    try:
        resp = ec2.describe_instances(Filters=[{"Name": "instance-state-name", "Values": ["running"]}])
        total_cost = 0
        instance_details = []
        for r in resp.get("Reservations", []):
            for i in r["Instances"]:
                itype = i['InstanceType']
                cost = INSTANCE_PRICE_MAP.get(itype, 0)
                total_cost += cost
                # --- FIX IS HERE: Escaped the parentheses ---
                instance_details.append(f"`{i['InstanceId']}` \({itype}\): `${cost}/hr`")

        hourly = total_cost
        daily = hourly * 24
        monthly = daily * 30

        msg = (
                f"*Estimated EC2 Running Costs:*\n\n"
                f"*Hourly:* `${hourly:.4f}`\n"
                f"*Daily:* `${daily:.2f}`\n"
                f"*Monthly:* `${monthly:.2f}`\n\n"
                f"*Running Instances:*\n" + "\n".join(instance_details)
        )
        update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
    except Exception as e:
        logger.error("Error in /cost command: %s", e)
        update.message.reply_text(f"Error calculating cost: {e}")


# --- Callback Handler for Inline Buttons ---
def handle_callback(update, context):
    query = update.callback_query
    query.answer()
    user = query.from_user

    if not (user.id in ADMIN_USERS or user.id in STANDARD_USERS):
        return query.edit_message_text("Unauthorized.")

    action, data = query.data.split(":", 1)
    iid = data
    log_action(user.id, f"callback:{action}", iid)

    try:
        # User-level actions
        if action == "start":
            ec2.start_instances(InstanceIds=[iid])
            query.edit_message_text(f"🟢 Start initiated for `{iid}`", parse_mode=ParseMode.MARKDOWN_V2)
        elif action == "stop":
            ec2.stop_instances(InstanceIds=[iid])
            query.edit_message_text(f"🔴 Stop initiated for `{iid}`", parse_mode=ParseMode.MARKDOWN_V2)
        elif action == "reboot":
            ec2.reboot_instances(InstanceIds=[iid])
            query.edit_message_text(f"🔄 Reboot initiated for `{iid}`", parse_mode=ParseMode.MARKDOWN_V2)
        elif action == "cancel":
            original_message = query.message.text
            query.edit_message_text(original_message, parse_mode=ParseMode.MARKDOWN_V2)  # Revert to original text

        # Admin-only actions
        elif action in ["terminate_confirm1", "terminate_confirm2"]:
            if user.id not in ADMIN_USERS:
                return query.edit_message_text("Admin access required to terminate.")

            if action == "terminate_confirm1":
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚨 YES, I AM SURE", callback_data=f"terminate_confirm2:{iid}")],
                    [InlineKeyboardButton("Cancel", callback_data=f"cancel:{iid}")]
                ])
                query.edit_message_text(f"⚠️ *ARE YOU ABSOLUTELY SURE* you want to terminate `{iid}`?",
                                        reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN_V2)

            elif action == "terminate_confirm2":
                ec2.terminate_instances(InstanceIds=[iid])
                query.edit_message_text(f"💥 Termination started for `{iid}`", parse_mode=ParseMode.MARKDOWN_V2)

    except Exception as e:
        query.edit_message_text(f"Error: {e}")


# --- Daily Report Scheduler ---
def daily_report(context):
    try:
        resp = ec2.describe_instances(Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}])
        running = []
        stopped = []
        total_cost = 0

        for r in resp.get("Reservations", []):
            for i in r["Instances"]:
                name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), i['InstanceId'])
                if i['State']['Name'] == 'running':
                    running.append(name)
                    total_cost += INSTANCE_PRICE_MAP.get(i['InstanceType'], 0)
                else:
                    stopped.append(name)

        daily_cost = total_cost * 24
        msg = (
                f"☀️ *AWS Daily Report*\n\n"
                f"🟢 *Running ({len(running)}):*\n" + (', '.join(running) if running else "None") + "\n\n"
                                                                                                   f"🔴 *Stopped ({len(stopped)}):*\n" + (
                    ', '.join(stopped) if stopped else "None") + "\n\n"
                                                                 f"💰 *Est\. Daily Cost:* `${daily_cost:.2f}`"
        )
        context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=msg, parse_mode=ParseMode.MARKDOWN_V2)
        log_action("SYSTEM", "daily_report", "Report sent successfully")
    except Exception as e:
        logger.error("Failed to send daily report: %s", e)
        log_action("SYSTEM", "daily_report", f"Failed: {e}")


# --- Main Bot Setup ---
def main():
    updater = Updater(token=TOKEN, use_context=True)
    dp = updater.dispatcher

    # Command Menu
    commands = [
        BotCommand("start", "▶️ Start the bot"),
        BotCommand("help", "❓ Show help"),
        BotCommand("list", "📜 List EC2 instances"),
        BotCommand("describe", "ℹ️ Get instance details"),
        BotCommand("cost", "💰 Estimate running costs"),
        BotCommand("allocate_eip", "➕ Allocate EIP (Admin)"),
        BotCommand("release_eip", "➖ Release EIP (Admin)"),
        BotCommand("add_ip", "🔒 Add IP to SG (Admin)"),
        BotCommand("remove_ip", "🔓 Remove IP from SG (Admin)"),
    ]
    updater.bot.set_my_commands(commands)

    # Command Handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("list", list_instances))
    dp.add_handler(CommandHandler("describe", describe_instance))
    dp.add_handler(CommandHandler("allocate_eip", allocate_eip))
    dp.add_handler(CommandHandler("associate_eip", associate_eip))
    dp.add_handler(CommandHandler("release_eip", release_eip))
    dp.add_handler(CommandHandler("add_ip", add_ip_to_sg))
    dp.add_handler(CommandHandler("remove_ip", remove_ip_from_sg))
    dp.add_handler(CommandHandler("cost", cost_command))
    dp.add_handler(CallbackQueryHandler(handle_callback))

    # Job Scheduler for Daily Report
    jq = updater.job_queue
    # Schedule for 9:00 AM IST
    jq.run_daily(daily_report, time=datetime.time(hour=9, minute=0, tzinfo=pytz.timezone('Asia/Kolkata')))

    logger.info("Bot started successfully!")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()