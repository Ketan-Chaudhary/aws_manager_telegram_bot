# AWS Manager Telegram Bot 🤖⚡

A lightweight **Telegram bot** to manage your **AWS EC2 instances** directly from chat.
Built with **Python + Boto3 + python-telegram-bot**, this project blends **Cloud/DevOps automation** with a clean, user-friendly interface.

---

## 🚀 Features

* 📜 **List EC2 Instances**: Shows instance ID, name, and state with status emojis
* 🟢 **Start Instances**: `/start_instance <instance-id>`
* 🔴 **Stop Instances**: `/stop_instance <instance-id>`
* 🔄 **Reboot Instances**: `/reboot_instance <instance-id>`
* 💥 **Terminate Instances**: With confirmation button (safety first ✅)
* 🔐 **User Authorization**: Only whitelisted Telegram IDs can use the bot
* 🔑 **Secure Token Management**: Bot token fetched from **AWS SSM Parameter Store**
* 🎛 **Professional Touch**: `/` menu shows available commands with descriptions

---

## 🛠️ Tech Stack

* **Python 3.12**
* **boto3** (AWS SDK for Python)
* **python-telegram-bot**
* **AWS EC2 & IAM**
* **AWS Systems Manager Parameter Store**

---

## 📦 Installation & Setup

### 1. Clone the Repository

```bash
git clone https://github.com/Ketan-Chaudhary/aws_manager_telegram_bo
cd aws-manager-telegram-bot
```

### 2. Create a Virtual Environment

```bash
python3 -m venv myenv
source myenv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure AWS

Ensure the bot is running on an EC2 instance with an IAM role that has at least:

* `ec2:DescribeInstances`
* `ec2:StartInstances`
* `ec2:StopInstances`
* `ec2:RebootInstances`
* `ec2:TerminateInstances`
* `ssm:GetParameter`

### 5. Store Bot Token in Parameter Store

```bash
aws ssm put-parameter \
  --name "/telegram/bot_token" \
  --type "SecureString" \
  --value "YOUR_BOT_TOKEN" \
  --region ap-south-1
```

### 6. Run the Bot

```bash
nohup python main.py &
```

---

## 💻 Usage

Start a chat with your bot on Telegram and try:

* `/start` → Welcome message
* `/help` → Show available commands
* `/list` → List EC2 instances
* `/start_instance i-xxxxxxxxxxxxx` → Start an instance
* `/stop_instance i-xxxxxxxxxxxxx` → Stop an instance
* `/reboot_instance i-xxxxxxxxxxxxx` → Reboot an instance
* `/terminate i-xxxxxxxxxxxxx` → Terminate an instance (with confirmation)

---

## 🔐 Security Notes

* Only whitelisted Telegram user IDs can interact with the bot
* Bot token is never stored in code, only fetched securely from AWS Parameter Store
* Sensitive actions (like termination) require explicit confirmation

---

## 📌 Roadmap / Future Enhancements

* 📊 Add CloudWatch metrics (CPU, Memory, etc.)
* 🌐 Support for other AWS resources (RDS, S3, Lambda)
* 🏗️ Dockerize for easier deployment
* 🔔 Notifications for instance state changes

---

## 🤝 Contributing

PRs and issues are welcome! Please open a discussion if you’d like to suggest new features.

---
