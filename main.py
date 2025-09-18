import boto3, os, re, logging
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Updater, CommandHandler, CallbackQueryHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

region = os.getenv("AWS_REGION", "ap-south-1")
ssm = boto3.client("ssm", region_name=region)
ec2 = boto3.client("ec2", region_name=region)

# --- Load bot token from Parameter Store ---
def get_bot_token():
    resp = ssm.get_parameter(Name="/telegram/bot_token", WithDecryption=True)
    return resp["Parameter"]["Value"]

TOKEN = get_bot_token()

# --- Whitelisted users (replace with your Telegram ID) ---
ALLOWED_USERS = [8498983488]

INSTANCE_RE = re.compile(r"^i-[0-9a-fA-F]{8,17}$")

def authorized(user_id):
    return user_id in ALLOWED_USERS

# --- START / WELCOME ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update.effective_user.id):
        return await update.message.reply_text("Unauthorized.")
    welcome = (
        "👋 Welcome to your AWS Manager Bot!\n\n"
        "I can help you manage your AWS EC2 instances directly from Telegram.\n"
        "Type /help to see all available commands."
    )
    await update.message.reply_text(welcome)

# --- HELP / COMMAND LIST ---
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update.effective_user.id):
        return await update.message.reply_text("Unauthorized.")
    commands = (
        "📌 *Available Commands:*\n\n"
        "/list – List all EC2 instances\n"
        "/terminate <instance-id> – Terminate an instance\n"
        "/start_instance <id> – Start an instance\n"
        "/stop_instance <id> – Stop an instance\n"
        "/reboot_instance <id> – Reboot an instance\n"
    )
    await update.message.reply_text(commands, parse_mode=ParseMode.MARKDOWN)

# --- LIST INSTANCES ---
def list_instances(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")
    resp = ec2.describe_instances()
    lines = []
    for r in resp.get("Reservations", []):
        for i in r["Instances"]:
            iid = i["InstanceId"]
            state = i["State"]["Name"]
            name = next((t["Value"] for t in i.get("Tags", []) if t["Key"]=="Name"), "")
            lines.append(f"{iid} — {name or '-'} — {state}")
    update.message.reply_text("\n".join(lines) or "No instances found.")

# --- TERMINATE INSTANCE ---
def terminate_instance(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")
    if len(context.args) != 1 or not INSTANCE_RE.match(context.args[0]):
        return update.message.reply_text("Usage: /terminate <instance-id>")
    iid = context.args[0]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Confirm Terminate", callback_data=f"terminate:{iid}"),
         InlineKeyboardButton("Cancel", callback_data="cancel")]
    ])
    update.message.reply_text(f"Confirm termination for {iid}?", reply_markup=keyboard)

# --- START INSTANCE ---
def start_instance(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")
    if len(context.args) != 1 or not INSTANCE_RE.match(context.args[0]):
        return update.message.reply_text("Usage: /start_instance <instance-id>")
    iid = context.args[0]
    ec2.start_instances(InstanceIds=[iid])
    update.message.reply_text(f"✅ Start initiated for {iid}")

# --- STOP INSTANCE ---
def stop_instance(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")
    if len(context.args) != 1 or not INSTANCE_RE.match(context.args[0]):
        return update.message.reply_text("Usage: /stop_instance <instance-id>")
    iid = context.args[0]
    ec2.stop_instances(InstanceIds=[iid])
    update.message.reply_text(f"🛑 Stop initiated for {iid}")

# --- REBOOT INSTANCE ---
def reboot_instance(update, context):
    if not authorized(update.effective_user.id):
        return update.message.reply_text("Unauthorized.")
    if len(context.args) != 1 or not INSTANCE_RE.match(context.args[0]):
        return update.message.reply_text("Usage: /reboot_instance <instance-id>")
    iid = context.args[0]
    ec2.reboot_instances(InstanceIds=[iid])
    update.message.reply_text(f"🔄 Reboot initiated for {iid}")

# --- CALLBACK HANDLER (Terminate Confirmation) ---
def handle_callback(update, context):
    query = update.callback_query
    query.answer()
    user = query.from_user
    if not authorized(user.id):
        return query.edit_message_text("Unauthorized.")

    data = query.data
    if data.startswith("terminate:"):
        iid = data.split(":")[1]
        ec2.terminate_instances(InstanceIds=[iid])
        query.edit_message_text(f"Termination started for {iid}.")
    elif data == "cancel":
        query.edit_message_text("Cancelled.")

# --- MAIN BOT ---
def main():
    updater = Updater(token=TOKEN, use_context=True)
    dp = updater.dispatcher

    # Handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("list", list_instances))
    dp.add_handler(CommandHandler("terminate", terminate_instance))
    dp.add_handler(CommandHandler("start_instance", start_instance))
    dp.add_handler(CommandHandler("stop_instance", stop_instance))
    dp.add_handler(CommandHandler("reboot_instance", reboot_instance))
    dp.add_handler(CallbackQueryHandler(handle_callback))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
