import boto3, os, re, logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
from telegram import ParseMode
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler

# --- Basic Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

region = os.getenv("AWS_REGION", "ap-south-1")
ssm = boto3.client("ssm", region_name=region)
ec2 = boto3.client("ec2", region_name=region)


# --- Load bot token from Parameter Store ---
def get_bot_token():
    try:
        resp = ssm.get_parameter(Name="/telegram/bot_token", WithDecryption=True)
        return resp["Parameter"]["Value"]
    except Exception as e:
        logger.error("Could not retrieve bot token from SSM Parameter Store. Exiting.")
        logger.error(e)
        exit()


TOKEN = get_bot_token()

# --- Whitelisted users ---
# IMPORTANT: Replace 8498983488 with your actual Telegram User ID
ALLOWED_USERS = [8498983488]
INSTANCE_RE = re.compile(r"^i-[0-9a-fA-F]{8,17}$")


def authorized(user_id):
    """Checks if a user is authorized."""
    return user_id in ALLOWED_USERS


# --- START / WELCOME ---
def start(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")
    welcome = (
        "👋 Welcome to your AWS Manager Bot!\n\n"
        "I can help you manage your AWS EC2 instances directly from Telegram.\n"
        "Type /help to see all available commands."
    )
    update.message.reply_text(welcome)


# --- HELP / COMMAND LIST (Improved Version) ---
def help_command(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")

    commands = (
        "📌 *Available Commands:*\n\n"
        "`/list` – List all EC2 instances\n"
        "`/terminate <instance-id>` – Terminate an instance\n"
        "`/start_instance <instance-id>` – Start an instance\n"
        "`/stop_instance <instance-id>` – Stop an instance\n"
        "`/reboot_instance <instance-id>` – Reboot an instance"
    )
    update.message.reply_text(commands, parse_mode=ParseMode.MARKDOWN_V2)


# --- LIST INSTANCES (Improved Version) ---
def list_instances(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")

    resp = ec2.describe_instances(Filters=[
        {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']}
    ])

    lines = []
    header = f"{'State':<5} {'Instance ID':<22} {'Name'}"
    lines.append(header)
    lines.append("-" * 45)

    status_emoji = {"running": "🟢", "stopped": "🔴", "pending": "⏳", "stopping": "⏳"}

    instances_found = False
    for r in resp.get("Reservations", []):
        for i in r["Instances"]:
            instances_found = True
            iid = i["InstanceId"]
            state = i["State"]["Name"]
            emoji = status_emoji.get(state, "❓")
            name = next((t["Value"] for t in i.get("Tags", []) if t["Key"] == "Name"), "-")
            lines.append(f"{emoji:<5} {iid:<22} {name}")

    if not instances_found:
        return update.message.reply_text("No running or stopped instances found.")

    message_text = "```\n" + "\n".join(lines) + "\n```"
    update.message.reply_text(message_text, parse_mode=ParseMode.MARKDOWN_V2)


# --- TERMINATE INSTANCE ---
def terminate_instance(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")
    if len(context.args) != 1 or not INSTANCE_RE.match(context.args[0]):
        return update.message.reply_text("Usage: `/terminate <instance-id>`", parse_mode=ParseMode.MARKDOWN_V2)
    iid = context.args[0]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Confirm Terminate", callback_data=f"terminate:{iid}"),
         InlineKeyboardButton("Cancel", callback_data="cancel")]
    ])
    update.message.reply_text(f"Confirm termination for `{iid}`?", reply_markup=keyboard,
                              parse_mode=ParseMode.MARKDOWN_V2)


# --- START INSTANCE ---
def start_instance(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")
    if len(context.args) != 1 or not INSTANCE_RE.match(context.args[0]):
        return update.message.reply_text("Usage: `/start_instance <instance-id>`", parse_mode=ParseMode.MARKDOWN_V2)
    iid = context.args[0]
    ec2.start_instances(InstanceIds=[iid])
    update.message.reply_text(f"✅ Start initiated for `{iid}`", parse_mode=ParseMode.MARKDOWN_V2)


# --- STOP INSTANCE ---
def stop_instance(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")
    if len(context.args) != 1 or not INSTANCE_RE.match(context.args[0]):
        return update.message.reply_text("Usage: `/stop_instance <instance-id>`", parse_mode=ParseMode.MARKDOWN_V2)
    iid = context.args[0]
    ec2.stop_instances(InstanceIds=[iid])
    update.message.reply_text(f"🛑 Stop initiated for `{iid}`", parse_mode=ParseMode.MARKDOWN_V2)


# --- REBOOT INSTANCE ---
def reboot_instance(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")
    if len(context.args) != 1 or not INSTANCE_RE.match(context.args[0]):
        return update.message.reply_text("Usage: `/reboot_instance <instance-id>`", parse_mode=ParseMode.MARKDOWN_V2)
    iid = context.args[0]
    ec2.reboot_instances(InstanceIds=[iid])
    update.message.reply_text(f"🔄 Reboot initiated for `{iid}`", parse_mode=ParseMode.MARKDOWN_V2)


# --- CALLBACK HANDLER ---
def handle_callback(update, context):
    query = update.callback_query
    query.answer()
    user = query.from_user
    if not authorized(user.id):
        return query.edit_message_text("Unauthorized.")

    data = query.data
    if data.startswith("terminate:"):
        iid = data.split(":")[1]
        try:
            ec2.terminate_instances(InstanceIds=[iid])
            query.edit_message_text(f"💥 Termination started for `{iid}`.", parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            logger.error(f"Failed to terminate {iid}: {e}")
            query.edit_message_text(f"Error terminating instance: {e}")
    elif data == "cancel":
        query.edit_message_text("Cancelled.")


# --- MAIN BOT ---
def main():
    updater = Updater(token=TOKEN, use_context=True)
    dp = updater.dispatcher

    # --- Define and Set Bot Commands ---
    commands = [
        BotCommand("start", "▶️ Start the bot"),
        BotCommand("help", "❓ Show help message"),
        BotCommand("list", "📜 List all EC2 instances"),
        BotCommand("start_instance", "🟢 Start an instance"),
        BotCommand("stop_instance", "🔴 Stop an instance"),
        BotCommand("reboot_instance", "🔄 Reboot an instance"),
        BotCommand("terminate", "💥 Terminate an instance"),
    ]
    updater.bot.set_my_commands(commands)

    # --- Register Handlers ---
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("list", list_instances))
    dp.add_handler(CommandHandler("terminate", terminate_instance))
    dp.add_handler(CommandHandler("start_instance", start_instance))
    dp.add_handler(CommandHandler("stop_instance", stop_instance))
    dp.add_handler(CommandHandler("reboot_instance", reboot_instance))
    dp.add_handler(CallbackQueryHandler(handle_callback))

    logger.info("Bot started successfully!")
    updater.start_polling()
    updater.idle()


if __name__ == "__main__":
    main()