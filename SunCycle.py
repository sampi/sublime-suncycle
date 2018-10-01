import sublime
from datetime import datetime,timedelta
from pytz import timezone
import pytz
from os import path
import calendar,json

import urllib.request as urllib
from .sun import Sun

from .package_control_download_wrapper import fetch

INTERVAL = 0.3 # interval in minutes to do new cycle check

IP_URL = 'http://ip-api.com/json'
CACHE_LIFETIME = timedelta(hours=12)

PACKAGE = path.splitext(path.basename(__file__))[0]

def logToConsole(str):
    print(PACKAGE + ': {0}'.format(str))

class Settings():
    def __init__(self, onChange=None):
        self.loaded = False
        self.onChange = onChange
        self.sun = None
        self.coordinates = None
        self.timezone = None
        self.timezoneName = None
        self.fixedTimes = None

        self.load()

    def _needsIpCacheRefresh(self, datetime):
        if not self._ipcache:
            return True

        return self._ipcache['date'] < (datetime.now() - CACHE_LIFETIME)

    def _needsTzCacheRefresh(self, datetime):
        if not self._tzcache:
            return True

        if self._tzcache['fixedCoordinates'] != self.fixedCoordinates:
            return True

        if self._tzcache['coordinates'] != self.coordinates:
            return True

        return self._tzcache['date'] < (datetime.now() - CACHE_LIFETIME)

    def _callJsonApi(self, url):
        try:
            # on Linux the embedded Python has no SSL support, so we use Package Control's downloader
            return json.loads(fetch(url).decode('utf-8'))
        except Exception as err:
            logToConsole(err)
            logToConsole('Failed to get a result from {0}'.format(url))

    def _getIPData(self):
        return self._callJsonApi(IP_URL)

    def getSun(self):
        if self.fixedCoordinates:
            # settings contain fixed values
            if not self.sun:
                self.sun = Sun(self.coordinates)
            return self.sun

        now = datetime.utcnow()
        try:
            if self._needsIpCacheRefresh(now):
                result = self._getIPData()
                self._ipcache = {'date': now}
                if 'lat' in result and 'lon' in result and 'timezone' in result:
                    self.coordinates = {'latitude': result['lat'], 'longitude': result['lon']}
                    logToConsole('Using location [{0[latitude]}, {0[longitude]}] from IP lookup'.format(self.coordinates))
                    self.sun = Sun(self.coordinates)
                    self.timezoneName = result['timezone']
        except TypeError:
            # Greenwich coordinates
            self.coordinates = {'latitude': 51.2838, 'longitude': 0}
            logToConsole('Using location [{0[latitude]}, {0[longitude]}] from Greenwich'.format(self.coordinates))
            self.sun = Sun(self.coordinates)
            self.timezoneName = 'UTC'

        if (self.sun):
            return self.sun
        else:
            raise KeyError('SunCycle: no coordinates')

    def getTimezone(self):
        now = datetime.utcnow()

        if self._needsTzCacheRefresh(now):
            self._tzcache = {
                'date': now,
                'fixedCoordinates': self.fixedCoordinates,
                'coordinates': self.coordinates,
                'timezoneName': self.timezoneName
            }
            if self.timezoneName:
                self.timezone = timezone(self.timezoneName)
            else:
                self.timezone = pytz.utc
            logToConsole('Using {0}'.format(self.timezone.tzname(now)))

        return self.timezone

    def getFixedTimes(self):
        return self.fixedTimes

    def load(self):
        settings = self._sublimeSettings = sublime.load_settings(PACKAGE + '.sublime-settings')
        settings.clear_on_change(PACKAGE)
        settings.add_on_change(PACKAGE, self.load)

        if not settings.has('day'):
            raise KeyError('SunCycle: missing day setting')

        if not settings.has('night'):
            raise KeyError('SunCycle: missing night setting')

        self._tzcache = None
        self._ipcache = None

        self.day = settings.get('day')
        self.night = settings.get('night')

        self.fixedCoordinates = False
        if settings.has('latitude') and settings.has('longitude'):
            self.fixedCoordinates = True
            self.coordinates = {'latitude': settings.get('latitude'), 'longitude': settings.get('longitude')}
            logToConsole('Using location [{0[latitude]}, {0[longitude]}] from settings'.format(self.coordinates))

        sun = self.getSun()
        now = self.getTimezone().localize(datetime.now())

        fixedSunrise = settings.get('sunrise')
        fixedSunset = settings.get('sunset')
        if fixedSunrise and fixedSunset:
            self.fixedTimes = {
                'sunrise': self.getTimezone().localize(datetime.combine(datetime.now(), datetime.strptime(fixedSunrise, '%H:%M').time())),
                'sunset': self.getTimezone().localize(datetime.combine(datetime.now(), datetime.strptime(fixedSunset, '%H:%M').time()))
            }
            logToConsole('Fixed sunrise at {0}'.format(self.fixedTimes['sunrise']))
            logToConsole('Fixed sunset at {0}'.format(self.fixedTimes['sunset']))
        else:
            logToConsole('Sunrise at {0}'.format(sun.sunrise(now)))
            logToConsole('Sunset at {0}'.format(sun.sunset(now)))

        if self.loaded and self.onChange:
            self.onChange()

        self.loaded = True
    def unload(self):
        self._sublimeSettings.clear_on_change(PACKAGE)
        self.loaded = False

class SunCycle():
    def __init__(self):
        self.dayPart = None
        self.halt = False
        sublime.set_timeout(self.start, 500) # delay execution so settings can load

    def getDayOrNight(self):
        sun = self.settings.getSun()
        now = self.settings.getTimezone().localize(datetime.now())
        fixedTimes = self.settings.getFixedTimes()
        if fixedTimes:
            return 'day' if now >= fixedTimes['sunrise'] and now <= fixedTimes['sunset'] else 'night'
        else:
            return 'day' if now >= sun.sunrise(now) and now <= sun.sunset(now) else 'night'

    def cycle(self):
        sublimeSettings = sublime.load_settings('Preferences.sublime-settings')

        if sublimeSettings is None:
            raise Exception('Preferences not loaded')

        config = getattr(self.settings, self.getDayOrNight())

        sublimeSettingsChanged = False

        newColorScheme = config.get('color_scheme')
        if newColorScheme and newColorScheme != sublimeSettings.get('color_scheme'):
            logToConsole('Switching to color scheme {0}'.format(newColorScheme))
            sublimeSettings.set('color_scheme', newColorScheme)
            sublimeSettingsChanged = True

        newTheme = config.get('theme')
        if newTheme and newTheme != sublimeSettings.get('theme'):
            logToConsole('Switching to theme {0}'.format(newTheme))
            sublimeSettings.set('theme', newTheme)
            sublimeSettingsChanged = True

        if sublimeSettingsChanged:
            sublime.save_settings('Preferences.sublime-settings')

    def start(self):
        self.settings = Settings(onChange=self.cycle)
        self.loop()

    def loop(self):
        if not self.halt:
            sublime.set_timeout(self.loop, INTERVAL * 60000)
            self.cycle()

    def stop(self):
        self.halt = True
        if hasattr(self, 'settings'):
            self.settings.unload()

# stop previous instance
if 'sunCycle' in globals():
    globals()['sunCycle'].stop()

# start cycle
sunCycle = SunCycle()
