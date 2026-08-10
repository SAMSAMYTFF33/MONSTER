import time

try:
    while True:
        time.sleep(50)  # خمول لمدة ثانية واحدة في كل دورة
except KeyboardInterrupt:
    print("\nتم إيقاف البرنامج بنجاح.")
