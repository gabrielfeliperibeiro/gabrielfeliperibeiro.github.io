#!/usr/bin/env python3
"""
Djokobilas - Tennis Court Booking Automation for Hong Kong
===========================================================
Automates the booking of tennis courts through the LCSD (Leisure and
Cultural Services Department) online booking system in Hong Kong.

Author: Gabriel Felipe Ribeiro
License: MIT
"""

import argparse
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait, Select
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.common.exceptions import (
        TimeoutException,
        NoSuchElementException,
        ElementClickInterceptedException,
        StaleElementReferenceException
    )
except ImportError:
    print("Error: selenium is not installed. Please run: pip install selenium")
    sys.exit(1)

try:
    from webdriver_manager.chrome import ChromeDriverManager
except ImportError:
    print("Warning: webdriver-manager not installed. You'll need to manage chromedriver manually.")
    ChromeDriverManager = None


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('booking.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


# LCSD Booking System URLs
LCSD_URLS = {
    'main': 'https://w1.leisurelink.lcsd.gov.hk/leisurelink/application/checkLogin.do',
    'booking': 'https://w1.leisurelink.lcsd.gov.hk/leisurelink/application/sportFac.do',
    'login': 'https://w1.leisurelink.lcsd.gov.hk/leisurelink/application/login.do',
}

# Popular Tennis Court Venues in Hong Kong
HK_TENNIS_VENUES = {
    # Hong Kong Island
    'victoria_park': {
        'name': 'Victoria Park Tennis Centre',
        'district': 'Causeway Bay',
        'courts': 14,
        'code': 'VP'
    },
    'hong_kong_park': {
        'name': 'Hong Kong Park Tennis Centre',
        'district': 'Central',
        'courts': 6,
        'code': 'HKP'
    },
    'wong_nai_chung': {
        'name': 'Wong Nai Chung Gap Road Tennis Courts',
        'district': 'Happy Valley',
        'courts': 6,
        'code': 'WNC'
    },
    'bowen_road': {
        'name': 'Bowen Road Tennis Courts',
        'district': 'Mid-Levels',
        'courts': 4,
        'code': 'BWR'
    },

    # Kowloon
    'kowloon_tsai': {
        'name': 'Kowloon Tsai Park Tennis Courts',
        'district': 'Kowloon City',
        'courts': 6,
        'code': 'KTP'
    },
    'morse_park': {
        'name': 'Morse Park Tennis Courts',
        'district': 'Wong Tai Sin',
        'courts': 8,
        'code': 'MP'
    },
    'king_george_v': {
        'name': 'King George V Memorial Park Tennis Courts',
        'district': 'Jordan',
        'courts': 4,
        'code': 'KGV'
    },

    # New Territories
    'sha_tin': {
        'name': 'Sha Tin Sports Ground Tennis Courts',
        'district': 'Sha Tin',
        'courts': 8,
        'code': 'STS'
    },
    'tuen_mun': {
        'name': 'Tuen Mun Tang Shiu Kin Sports Ground',
        'district': 'Tuen Mun',
        'courts': 6,
        'code': 'TMT'
    },
    'tin_shui_wai': {
        'name': 'Tin Shui Wai Sports Ground',
        'district': 'Tin Shui Wai',
        'courts': 4,
        'code': 'TSW'
    }
}

# Time slots available for booking (1-hour slots)
TIME_SLOTS = [
    '07:00', '08:00', '09:00', '10:00', '11:00', '12:00',
    '13:00', '14:00', '15:00', '16:00', '17:00', '18:00',
    '19:00', '20:00', '21:00', '22:00'
]


class BookingConfig:
    """Configuration handler for booking preferences."""

    def __init__(self, config_path: str = 'config.json'):
        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """Load configuration from JSON file."""
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                return json.load(f)
        return self._default_config()

    def _default_config(self) -> Dict:
        """Return default configuration."""
        return {
            'username': '',
            'password': '',
            'preferred_venues': ['victoria_park', 'hong_kong_park'],
            'preferred_times': ['18:00', '19:00', '20:00'],
            'preferred_days': ['Saturday', 'Sunday'],
            'booking_advance_days': 7,
            'max_retries': 3,
            'retry_delay': 5,
            'headless': False,
            'notification_email': '',
            'telegram_bot_token': '',
            'telegram_chat_id': ''
        }

    def save_config(self):
        """Save current configuration to file."""
        with open(self.config_path, 'w') as f:
            json.dump(self.config, f, indent=4)

    def get(self, key: str, default=None):
        """Get configuration value."""
        return self.config.get(key, default)

    def set(self, key: str, value):
        """Set configuration value."""
        self.config[key] = value


class NotificationService:
    """Handle notifications for booking status."""

    def __init__(self, config: BookingConfig):
        self.config = config
        self._setup_telegram()

    def _setup_telegram(self):
        """Setup Telegram bot if configured."""
        self.telegram_token = self.config.get('telegram_bot_token')
        self.telegram_chat_id = self.config.get('telegram_chat_id')
        self.telegram_enabled = bool(self.telegram_token and self.telegram_chat_id)

    def send_notification(self, message: str, success: bool = True):
        """Send notification through configured channels."""
        logger.info(f"Notification: {message}")

        if self.telegram_enabled:
            self._send_telegram(message)

    def _send_telegram(self, message: str):
        """Send Telegram notification."""
        try:
            import requests
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            data = {
                'chat_id': self.telegram_chat_id,
                'text': f"🎾 Djokobilas Tennis Booking\n\n{message}",
                'parse_mode': 'HTML'
            }
            requests.post(url, data=data, timeout=10)
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")


class TennisCourtBooker:
    """Main class for automating tennis court booking."""

    def __init__(self, config: BookingConfig):
        self.config = config
        self.driver = None
        self.wait = None
        self.notification = NotificationService(config)

    def _setup_driver(self):
        """Initialize Chrome WebDriver with appropriate options."""
        options = Options()

        if self.config.get('headless', False):
            options.add_argument('--headless')

        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,1080')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option('excludeSwitches', ['enable-automation'])
        options.add_experimental_option('useAutomationExtension', False)

        # Add random user agent
        user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
        ]
        options.add_argument(f'--user-agent={random.choice(user_agents)}')

        try:
            if ChromeDriverManager:
                service = Service(ChromeDriverManager().install())
                self.driver = webdriver.Chrome(service=service, options=options)
            else:
                self.driver = webdriver.Chrome(options=options)

            self.wait = WebDriverWait(self.driver, 20)

            # Stealth settings
            self.driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            logger.info("Chrome WebDriver initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize WebDriver: {e}")
            raise

    def _random_delay(self, min_sec: float = 0.5, max_sec: float = 2.0):
        """Add random delay to mimic human behavior."""
        time.sleep(random.uniform(min_sec, max_sec))

    def login(self) -> bool:
        """Log in to the LCSD booking system."""
        username = self.config.get('username')
        password = self.config.get('password')

        if not username or not password:
            logger.error("Username or password not configured")
            return False

        try:
            logger.info("Navigating to login page...")
            self.driver.get(LCSD_URLS['login'])
            self._random_delay(1, 2)

            # Wait for login form
            username_field = self.wait.until(
                EC.presence_of_element_located((By.NAME, 'loginName'))
            )
            password_field = self.driver.find_element(By.NAME, 'password')

            # Enter credentials with human-like typing
            for char in username:
                username_field.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))

            self._random_delay(0.3, 0.7)

            for char in password:
                password_field.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))

            self._random_delay(0.5, 1)

            # Submit login form
            submit_button = self.driver.find_element(By.CSS_SELECTOR, 'input[type="submit"]')
            submit_button.click()

            self._random_delay(2, 3)

            # Check if login was successful
            if 'logout' in self.driver.page_source.lower() or 'welcome' in self.driver.page_source.lower():
                logger.info("Login successful!")
                return True
            else:
                logger.error("Login failed - please check credentials")
                return False

        except TimeoutException:
            logger.error("Login page load timeout")
            return False
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    def navigate_to_tennis_booking(self) -> bool:
        """Navigate to the tennis court booking page."""
        try:
            logger.info("Navigating to tennis court booking...")
            self.driver.get(LCSD_URLS['booking'])
            self._random_delay(1, 2)

            # Select tennis from sports category
            sport_select = self.wait.until(
                EC.presence_of_element_located((By.NAME, 'category'))
            )
            Select(sport_select).select_by_visible_text('Tennis')
            self._random_delay(1, 2)

            logger.info("Tennis category selected")
            return True

        except Exception as e:
            logger.error(f"Failed to navigate to tennis booking: {e}")
            return False

    def select_venue(self, venue_key: str) -> bool:
        """Select a specific venue for booking."""
        if venue_key not in HK_TENNIS_VENUES:
            logger.error(f"Unknown venue: {venue_key}")
            return False

        venue = HK_TENNIS_VENUES[venue_key]
        logger.info(f"Selecting venue: {venue['name']}")

        try:
            # Wait for venue dropdown
            venue_select = self.wait.until(
                EC.presence_of_element_located((By.NAME, 'venue'))
            )

            # Try to select by visible text
            try:
                Select(venue_select).select_by_visible_text(venue['name'])
            except NoSuchElementException:
                # Try partial match
                options = venue_select.find_elements(By.TAG_NAME, 'option')
                for option in options:
                    if venue['district'] in option.text or venue['code'] in option.text:
                        option.click()
                        break

            self._random_delay(1, 2)
            logger.info(f"Venue {venue['name']} selected")
            return True

        except Exception as e:
            logger.error(f"Failed to select venue: {e}")
            return False

    def select_date(self, target_date: datetime) -> bool:
        """Select the target date for booking."""
        date_str = target_date.strftime('%d/%m/%Y')
        logger.info(f"Selecting date: {date_str}")

        try:
            date_input = self.wait.until(
                EC.presence_of_element_located((By.NAME, 'bookingDate'))
            )
            date_input.clear()
            date_input.send_keys(date_str)
            self._random_delay(0.5, 1)

            logger.info(f"Date {date_str} entered")
            return True

        except Exception as e:
            logger.error(f"Failed to select date: {e}")
            return False

    def find_available_slots(self) -> List[Dict]:
        """Find all available time slots for the selected venue and date."""
        available_slots = []

        try:
            # Click search/check availability button
            search_button = self.wait.until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[value="Search"]'))
            )
            search_button.click()
            self._random_delay(2, 3)

            # Look for available slots in the results table
            slots_table = self.wait.until(
                EC.presence_of_element_located((By.CLASS_NAME, 'resultTable'))
            )

            rows = slots_table.find_elements(By.TAG_NAME, 'tr')

            for row in rows[1:]:  # Skip header row
                cells = row.find_elements(By.TAG_NAME, 'td')
                if len(cells) >= 3:
                    time_slot = cells[0].text.strip()
                    court_num = cells[1].text.strip()
                    status = cells[2].text.strip().lower()

                    if 'available' in status or 'book' in status:
                        available_slots.append({
                            'time': time_slot,
                            'court': court_num,
                            'element': row
                        })

            logger.info(f"Found {len(available_slots)} available slots")
            return available_slots

        except TimeoutException:
            logger.warning("No slots table found - page may have changed")
            return []
        except Exception as e:
            logger.error(f"Error finding available slots: {e}")
            return []

    def book_slot(self, slot: Dict) -> bool:
        """Attempt to book a specific time slot."""
        logger.info(f"Attempting to book: Court {slot['court']} at {slot['time']}")

        try:
            # Find and click the book button in the slot row
            book_button = slot['element'].find_element(
                By.CSS_SELECTOR, 'input[type="submit"], button, a.book'
            )
            book_button.click()
            self._random_delay(2, 3)

            # Confirm booking if there's a confirmation page
            try:
                confirm_button = self.wait.until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, 'input[value="Confirm"]'))
                )
                confirm_button.click()
                self._random_delay(2, 3)
            except TimeoutException:
                pass  # No confirmation needed

            # Check for success message
            if 'success' in self.driver.page_source.lower() or 'confirmed' in self.driver.page_source.lower():
                logger.info(f"Successfully booked Court {slot['court']} at {slot['time']}!")
                self.notification.send_notification(
                    f"✅ Booking Confirmed!\n"
                    f"Court: {slot['court']}\n"
                    f"Time: {slot['time']}"
                )
                return True
            else:
                logger.warning("Booking may not have been successful")
                return False

        except Exception as e:
            logger.error(f"Failed to book slot: {e}")
            return False

    def run_booking_session(self) -> bool:
        """Execute a complete booking session."""
        try:
            self._setup_driver()

            # Login
            if not self.login():
                return False

            # Navigate to tennis booking
            if not self.navigate_to_tennis_booking():
                return False

            # Calculate target date
            advance_days = self.config.get('booking_advance_days', 7)
            target_date = datetime.now() + timedelta(days=advance_days)

            # Check if target date matches preferred days
            preferred_days = self.config.get('preferred_days', [])
            if preferred_days and target_date.strftime('%A') not in preferred_days:
                logger.info(f"Target date {target_date.strftime('%A')} not in preferred days")
                # Find next preferred day
                for i in range(1, 8):
                    check_date = datetime.now() + timedelta(days=advance_days + i)
                    if check_date.strftime('%A') in preferred_days:
                        target_date = check_date
                        break

            # Select date
            if not self.select_date(target_date):
                return False

            # Try each preferred venue
            preferred_venues = self.config.get('preferred_venues', ['victoria_park'])
            preferred_times = self.config.get('preferred_times', ['18:00', '19:00'])

            for venue_key in preferred_venues:
                if not self.select_venue(venue_key):
                    continue

                # Find available slots
                available_slots = self.find_available_slots()

                if not available_slots:
                    logger.info(f"No available slots at {venue_key}")
                    continue

                # Filter by preferred times
                for slot in available_slots:
                    slot_time = slot['time'].split('-')[0].strip()
                    if any(pref in slot_time for pref in preferred_times):
                        if self.book_slot(slot):
                            return True

                # If no preferred times available, try any available slot
                if available_slots:
                    logger.info("No preferred times available, trying any slot...")
                    if self.book_slot(available_slots[0]):
                        return True

            logger.warning("No bookings were made")
            self.notification.send_notification(
                "❌ No available slots found for your preferences"
            )
            return False

        except Exception as e:
            logger.error(f"Booking session error: {e}")
            return False

        finally:
            if self.driver:
                self.driver.quit()

    def run_with_retry(self) -> bool:
        """Run booking session with retry logic."""
        max_retries = self.config.get('max_retries', 3)
        retry_delay = self.config.get('retry_delay', 5)

        for attempt in range(1, max_retries + 1):
            logger.info(f"Booking attempt {attempt}/{max_retries}")

            if self.run_booking_session():
                return True

            if attempt < max_retries:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)

        logger.error("All booking attempts failed")
        return False


class ScheduledBooker:
    """Schedule bookings to run at optimal times."""

    def __init__(self, config: BookingConfig):
        self.config = config
        self.booker = TennisCourtBooker(config)

    def calculate_optimal_booking_time(self) -> datetime:
        """
        Calculate the optimal time to start booking.
        LCSD typically opens bookings at midnight (00:00) HK time,
        7 days in advance.
        """
        now = datetime.now()

        # Target is midnight tonight
        target = now.replace(hour=0, minute=0, second=0, microsecond=0)
        target += timedelta(days=1)

        # Start a few seconds before midnight
        target -= timedelta(seconds=5)

        return target

    def wait_until(self, target_time: datetime):
        """Wait until the specified time."""
        while datetime.now() < target_time:
            remaining = (target_time - datetime.now()).total_seconds()
            if remaining > 60:
                logger.info(f"Waiting... {remaining/60:.1f} minutes remaining")
                time.sleep(30)
            elif remaining > 5:
                time.sleep(1)
            else:
                time.sleep(0.1)

    def run_scheduled(self):
        """Run booking at the optimal scheduled time."""
        target_time = self.calculate_optimal_booking_time()
        logger.info(f"Scheduling booking for: {target_time}")

        self.wait_until(target_time)

        logger.info("Starting booking attempt!")
        return self.booker.run_with_retry()


def setup_wizard():
    """Interactive setup wizard for first-time configuration."""
    print("\n" + "="*60)
    print("🎾 Welcome to Djokobilas - Tennis Court Booking Automation")
    print("="*60 + "\n")

    config = BookingConfig()

    print("Let's set up your booking preferences.\n")

    # Username and password
    username = input("Enter your LCSD account username: ").strip()
    password = input("Enter your LCSD account password: ").strip()
    config.set('username', username)
    config.set('password', password)

    # Preferred venues
    print("\nAvailable venues:")
    for i, (key, venue) in enumerate(HK_TENNIS_VENUES.items(), 1):
        print(f"  {i}. {venue['name']} ({venue['district']})")

    venue_input = input("\nEnter venue numbers (comma-separated, e.g., 1,2,3): ").strip()
    try:
        venue_indices = [int(x.strip()) for x in venue_input.split(',')]
        venue_keys = list(HK_TENNIS_VENUES.keys())
        selected_venues = [venue_keys[i-1] for i in venue_indices if 0 < i <= len(venue_keys)]
        config.set('preferred_venues', selected_venues)
    except ValueError:
        print("Invalid input, using default venues")

    # Preferred times
    print(f"\nAvailable time slots: {', '.join(TIME_SLOTS)}")
    time_input = input("Enter preferred times (comma-separated, e.g., 18:00,19:00): ").strip()
    if time_input:
        times = [t.strip() for t in time_input.split(',') if t.strip() in TIME_SLOTS]
        if times:
            config.set('preferred_times', times)

    # Preferred days
    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    print(f"\nDays of the week: {', '.join(days)}")
    day_input = input("Enter preferred days (comma-separated, e.g., Saturday,Sunday): ").strip()
    if day_input:
        selected_days = [d.strip().capitalize() for d in day_input.split(',') if d.strip().capitalize() in days]
        if selected_days:
            config.set('preferred_days', selected_days)

    # Headless mode
    headless = input("\nRun in headless mode (no browser window)? (y/n): ").strip().lower()
    config.set('headless', headless == 'y')

    # Telegram notifications
    telegram = input("\nSet up Telegram notifications? (y/n): ").strip().lower()
    if telegram == 'y':
        bot_token = input("Enter Telegram bot token: ").strip()
        chat_id = input("Enter Telegram chat ID: ").strip()
        config.set('telegram_bot_token', bot_token)
        config.set('telegram_chat_id', chat_id)

    config.save_config()
    print("\n✅ Configuration saved to config.json")
    print("You can edit this file directly to modify settings.\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Djokobilas - Tennis Court Booking Automation for Hong Kong'
    )
    parser.add_argument('--setup', action='store_true', help='Run setup wizard')
    parser.add_argument('--book', action='store_true', help='Run booking immediately')
    parser.add_argument('--schedule', action='store_true', help='Schedule booking at optimal time')
    parser.add_argument('--config', type=str, default='config.json', help='Path to config file')
    parser.add_argument('--venues', action='store_true', help='List available venues')
    parser.add_argument('--headless', action='store_true', help='Run in headless mode')

    args = parser.parse_args()

    if args.venues:
        print("\n🎾 Available Tennis Court Venues in Hong Kong:\n")
        for key, venue in HK_TENNIS_VENUES.items():
            print(f"  {key}:")
            print(f"    Name: {venue['name']}")
            print(f"    District: {venue['district']}")
            print(f"    Courts: {venue['courts']}")
            print()
        return

    if args.setup:
        setup_wizard()
        return

    config = BookingConfig(args.config)

    if args.headless:
        config.set('headless', True)

    if not config.get('username') or not config.get('password'):
        print("No credentials found. Run with --setup to configure.")
        return

    if args.schedule:
        scheduler = ScheduledBooker(config)
        scheduler.run_scheduled()
    else:
        booker = TennisCourtBooker(config)
        booker.run_with_retry()


if __name__ == '__main__':
    main()
