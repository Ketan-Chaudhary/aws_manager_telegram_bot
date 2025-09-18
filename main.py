import boto3, os, json, re
import logging
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
ALLOWED_USERS = [123456789]

INSTANCE_RE = re.compile(r"^i-[0-9a-fA-F]{8,17}$")

def authorized(user_id):
    return user_id in ALLOWED_USERS

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

def handle_callback(update, context):
    query = update.callback_query
    query.answer()
    user = query.from_user
    if not authorized(user.id):
        return query.edit_message_text("Unauthorized.")

    data = query.data
    if data.startswith("terminate:"):
        iid = data.split(":")[1]
        # check AllowTerminate tag
        res = ec2.describe_instances(InstanceIds=[iid])
        tags = {t["Key"]: t["Value"] for t in res["Reservations"][0]["Instances"][0].get("Tags", [])}
        if tags.get("AllowTerminate") != "true":
            return query.edit_message_text("Termination not allowed (missing tag).")
        ec2.terminate_instances(InstanceIds=[iid])
        query.edit_message_text(f"Termination started for {iid}.")
    elif data == "cancel":
        query.edit_message_text("Cancelled.")

def main():
    updater = Updater(token=TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("list", list_instances))
    dp.add_handler(CommandHandler("terminate", terminate_instance))
    dp.add_handler(CallbackQueryHandler(handle_callback))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
