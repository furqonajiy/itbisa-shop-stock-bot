"""
test_telegram.py
----------------
Standalone script to verify that the Telegram bot is configured correctly.

Run this whenever you suspect the Telegram side is broken. It performs
three checks in order, with clear PASS or FAIL output for each:

  1. Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment.
  2. Calls Telegram's getMe endpoint to verify the bot token is valid.
  3. Sends a test text message to the configured chat.
  4. Sends a small test image (a 1x1 red pixel) to the same chat.

If any step fails, the error message tells you exactly what to fix.
This script does NOT depend on Shopee or any other part of the bot,
so a passing result confirms the Telegram side is fully working.

Usage:
  python scripts/test_telegram.py
"""

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

# Load .env from project root for local testing.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def main():
    """Runs all four checks in order, exiting on the first failure."""

    print("=" * 60)
    print("Telegram Bot Connectivity Test")
    print("=" * 60)

    # STEP 1: Verify the credentials were loaded.
    print("\n[1/4] Checking credentials loaded from environment...")
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not token:
        print("  ✗ FAIL: TELEGRAM_BOT_TOKEN is empty.")
        print("    Make sure it is set in your .env file.")
        sys.exit(1)
    if not chat_id:
        print("  ✗ FAIL: TELEGRAM_CHAT_ID is empty.")
        print("    Make sure it is set in your .env file.")
        sys.exit(1)

    # Show partial values so you can spot typos without leaking secrets to logs.
    print(f"  ✓ PASS: token starts with '{token[:10]}...', chat_id is '{chat_id}'")

    base_url = f"https://api.telegram.org/bot{token}"

    # STEP 2: Verify the bot token works by calling getMe.
    # This endpoint returns info about the bot itself and is the standard
    # way to confirm a token is valid without sending a message.
    print("\n[2/4] Verifying bot token with Telegram getMe endpoint...")
    try:
        response = requests.get(f"{base_url}/getMe", timeout=10)
    except requests.RequestException as e:
        print(f"  ✗ FAIL: network error reaching Telegram: {e}")
        print(f"    Check your internet connection.")
        sys.exit(1)

    if response.status_code != 200:
        print(f"  ✗ FAIL: Telegram returned HTTP {response.status_code}")
        print(f"    Response: {response.text}")
        print(f"    Most likely cause: TELEGRAM_BOT_TOKEN is wrong.")
        print(f"    Get a new token from @BotFather on Telegram.")
        sys.exit(1)

    bot_info = response.json().get("result", {})
    bot_username = bot_info.get("username", "?")
    bot_name = bot_info.get("first_name", "?")
    print(f"  ✓ PASS: connected to bot '{bot_name}' (@{bot_username})")

    # STEP 3: Send a test text message.
    # If this fails, the most common cause is that the user/group the chat_id
    # refers to has not started a conversation with the bot, or the chat_id
    # itself is wrong.
    print("\n[3/4] Sending test text message...")
    text_payload = {
        "chat_id": chat_id,
        "text": (
            "🧪 Test dari ITBisa Order Bot.\n"
            "\n"
            "Jika Anda melihat pesan ini, koneksi Telegram bekerja dengan baik."
        ),
    }
    response = requests.post(f"{base_url}/sendMessage", data=text_payload, timeout=10)

    if response.status_code != 200:
        print(f"  ✗ FAIL: Telegram returned HTTP {response.status_code}")
        print(f"    Response: {response.text}")
        print(f"    Most common causes:")
        print(f"      - TELEGRAM_CHAT_ID is wrong (typo or wrong chat).")
        print(f"      - For a group chat: bot is not added to the group,")
        print(f"        or the chat_id is missing the '-100' prefix.")
        print(f"      - For a personal chat: the user has not sent a message")
        print(f"        to the bot first. Telegram requires this before the")
        print(f"        bot can initiate contact.")
        sys.exit(1)

    print(f"  ✓ PASS: text message sent. Check your Telegram chat.")

    # STEP 4: Send a small test image to verify the photo endpoint also works.
    # This matters because the real bot uses sendPhoto, not sendMessage.
    print("\n[4/4] Sending test image...")
    test_png = _make_tiny_red_png()
    files = {"photo": ("test.png", test_png, "image/png")}
    image_data = {
        "chat_id": chat_id,
        "caption": "🧪 Test gambar dari ITBisa Order Bot",
    }
    response = requests.post(f"{base_url}/sendPhoto", files=files, data=image_data, timeout=15)

    if response.status_code != 200:
        print(f"  ✗ FAIL: sendPhoto returned HTTP {response.status_code}")
        print(f"    Response: {response.text}")
        sys.exit(1)

    print(f"  ✓ PASS: image sent. Check your Telegram chat.")

    # All four steps passed.
    print("\n" + "=" * 60)
    print("✓ All checks passed. Telegram is working correctly.")
    print("=" * 60)
    print("\nIf you still see issues with the main bot, the problem is")
    print("somewhere else (Shopee API, label generation, etc), not Telegram.")


def _make_tiny_red_png():
    """
    Returns the bytes of a 1x1 red PNG image.

    Hardcoded so this script has zero extra dependencies. We do not want to
    require Pillow or any image library just for a connectivity test.
    """
    # This is the smallest possible valid PNG file: 1x1 pixel, solid red.
    return bytes([
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,
        0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
        0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
        0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,
        0x54, 0x08, 0x99, 0x63, 0xF8, 0xCF, 0xC0, 0x00,
        0x00, 0x00, 0x03, 0x00, 0x01, 0x5B, 0xCB, 0x66,
        0x4C, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,
        0x44, 0xAE, 0x42, 0x60, 0x82,
    ])


if __name__ == "__main__":
    main()
