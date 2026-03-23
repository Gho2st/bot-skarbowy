# 🚗 Polish Treasury Auctions Bot

An automated script that monitors `skarbowe-licytacje.com` for vehicle bargains. It uses **Google Gemini AI** to extract data from unstructured web pages, PDFs, and DOCX files. 
If a vehicle's starting price is ≤ 50% of its estimated value (or it's a "free-hand" sale), the bot saves it and sends an email alert.

## ✨ Core Features
* **AI Parsing:** Extracts vehicle name, prices, and city directly from HTML, PDFs, and Word documents using Gemini AI.
* **Smart Filtering:** Flags only highly profitable deals (≤ 50% value or "sprzedaż z wolnej ręki").
* **Distance Calculator:** Automatically calculates the distance from the auction city to Kraków.
* **Email Alerts:** Sends an HTML email with the found bargains or a "No new deals today" notification.
* **History Tracking:** Remembers checked URLs (`historia_linkow.txt`) to save API calls.

## 🛠️ Setup & Requirements

1. Install required packages:
   ```bash
   pip install requests beautifulsoup4 google-generativeai
Set up your environment variables (or GitHub Secrets):

GEMINI_API_KEY - Your Google Gemini API key
EMAIL_SENDER - Your Gmail address for sending alerts
EMAIL_PASSWORD - Your Gmail App Password
EMAIL_RECEIVER - Comma-separated list of target emails

🚀 How to Run
Execute the script via terminal:

Bash
python bot.py
