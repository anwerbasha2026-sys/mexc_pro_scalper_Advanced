[app]
title = Mexc Pro Scalper Advanced
package.name = mexcproscalperadvanced
package.domain = org.mexcproscalperadvanced
source.dir = .
source.include_exts = py,png,jpg,jpeg,kv,json,txt,csv,proto
source.exclude_dirs = .git,.buildozer,bin,__pycache__
version = 1.3.2
requirements = python3==3.11.5,hostpython3==3.11.5,kivy==2.3.1,requests==2.32.5,httpx,websockets,protobuf==7.36.2,python-dotenv
orientation = portrait
fullscreen = 0
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WAKE_LOCK,FOREGROUND_SERVICE,POST_NOTIFICATIONS,READ_EXTERNAL_STORAGE,WRITE_EXTERNAL_STORAGE
android.api = 33
android.minapi = 23
android.ndk = 25b
android.ndk_api = 23
android.archs = armeabi-v7a
android.enable_androidx = True
android.accept_sdk_license = True
android.private_storage = True
android.debug_artifact = apk
android.logcat_filters = *:S python:I AndroidRuntime:E SDLThread:E

# Python-for-Android service declaration. It must remain in [app] so
# ServiceSniperd is generated and packaged into the APK.
services = sniperd:service.py:foreground:sticky

# Do not add pydantic/pydantic-settings here. They are not imported by this
# application, and pydantic v2 requires pydantic_core native components that
# are not reliably available in python-for-android ARMv7 builds. Their
# inclusion caused the runtime error: No module named 'pydantic_core'.

[buildozer]
log_level = 2
warn_on_root = 1

p4a.bootstrap = sdl2
p4a.branch = v2024.01.21
