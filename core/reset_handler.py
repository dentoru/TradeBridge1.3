import os
import time
import schedule
import pytz
from datetime import datetime
import json
import logging
import sys
from utils import StrategyTracker, load_config, shutdown_all

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(__file__), '..', 'logs', 'reset_handler.log'),
                          encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

class ResetHandler:
    def __init__(self):
        self.tracker = StrategyTracker()
        self.config = load_config()
        self.timezone = pytz.UTC
        self._load_timezone_config()

    def _load_timezone_config(self):
        if self.config and 'system' in self.config:
            tz_str = self.config['system'].get('timezone', 'UTC')
            try:
                self.timezone = pytz.timezone(tz_str)
                logging.info(f"Timezone set to: {tz_str}")
            except pytz.UnknownTimeZoneError:
                logging.warning(f"Unknown timezone '{tz_str}', defaulting to UTC")

    def _get_reset_time(self) -> str:
        return self.config['system'].get('daily_reset_time', '00:00') if self.config else '00:00'

    def daily_reset(self):
        logging.info("\n=== DAILY RESET INITIATED ===")
        try:
            self.config = load_config()
            if not self.config:
                raise ValueError("Config load failed")
                
            for strategy in self.config['strategies']:
                if self.config['strategies'][strategy]['enabled']:
                    self.tracker.reset_daily(strategy)
                    logging.info(f"Reset {strategy}")
                    
        except Exception as e:
            logging.error(f"Reset failed: {str(e)}")
        finally:
            shutdown_all()

    def run(self):
        reset_time = self._get_reset_time()
        if self.timezone != pytz.UTC:
            reset_dt = self.timezone.localize(
                datetime.strptime(reset_time, '%H:%M').replace(
                    year=datetime.now().year,
                    month=datetime.now().month,
                    day=datetime.now().day
                )
            )
            reset_time = reset_dt.astimezone(pytz.UTC).strftime('%H:%M')
        
        schedule.every().day.at(reset_time).do(self.daily_reset)
        
        logging.info("\n=== RESET HANDLER STARTED ===")
        logging.info(f"Next reset at {reset_time} UTC")
        
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)
        except KeyboardInterrupt:
            logging.info("\nShutting down...")
        finally:
            shutdown_all()

if __name__ == "__main__":
    handler = ResetHandler()
    handler.run()