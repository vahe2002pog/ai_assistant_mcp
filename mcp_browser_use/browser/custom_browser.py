import asyncio
import pdb

from playwright.async_api import Browser as PlaywrightBrowser
from playwright.async_api import (
    BrowserContext as PlaywrightBrowserContext,
)
from playwright.async_api import (
    Playwright,
    async_playwright,
)
from browser_use.browser.browser import Browser
from browser_use.browser.context import BrowserContext, BrowserContextConfig
from playwright.async_api import BrowserContext as PlaywrightBrowserContext
import logging

from .custom_context import CustomBrowserContext

logger = logging.getLogger(__name__)

class CustomBrowser(Browser):

    async def new_context(
        self,
        config: BrowserContextConfig = BrowserContextConfig()
    ) -> CustomBrowserContext:
        return CustomBrowserContext(config=config, browser=self)
    
    async def _setup_browser_with_instance(self, playwright: Playwright) -> PlaywrightBrowser:
        """Подключается к существующему Chrome или запускает новый с дефолтным профилем."""
        if not self.config.chrome_instance_path:
            raise ValueError('Chrome instance path is required')
        import subprocess
        import os
        import requests

        try:
            response = requests.get('http://localhost:9222/json/version', timeout=2)
            if response.status_code == 200:
                logger.info('Reusing existing Chrome instance')
                return await playwright.chromium.connect_over_cdp(
                    endpoint_url='http://localhost:9222',
                    timeout=20000,
                )
        except requests.ConnectionError:
            logger.debug('No existing Chrome instance found, starting a new one')

        # Путь к дефолтному профилю Chrome
        default_profile = os.path.join(
            os.environ.get('LOCALAPPDATA', ''),
            'Google', 'Chrome', 'User Data'
        )
        user_data_dir = os.environ.get('CHROME_USER_DATA_DIR', default_profile)

        subprocess.Popen(
            [
                self.config.chrome_instance_path,
                '--remote-debugging-port=9222',
                f'--user-data-dir={user_data_dir}',
            ] + self.config.extra_chromium_args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        for _ in range(10):
            try:
                response = requests.get('http://localhost:9222/json/version', timeout=2)
                if response.status_code == 200:
                    break
            except requests.ConnectionError:
                pass
            await asyncio.sleep(1)

        try:
            return await playwright.chromium.connect_over_cdp(
                endpoint_url='http://localhost:9222',
                timeout=20000,
            )
        except Exception as e:
            logger.error(f'Failed to connect to Chrome: {str(e)}')
            raise RuntimeError(
                'Close all Chrome windows and try again — Chrome must start fresh with debug port.'
            )