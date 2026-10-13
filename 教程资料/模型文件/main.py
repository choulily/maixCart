
try:
    import gc, lcd, image
    gc.collect()
    lcd.init()
    loading = image.Image(size=(lcd.width(), lcd.height()))
    loading.draw_rectangle((0, 0, lcd.width(), lcd.height()), fill=True, color=(0, 81, 137))
    info = "Welcome to Makerobo CanMV"
    loading.draw_string(int(lcd.width()//2 - len(info) * 5), (lcd.height())//4, info, color=(255, 255, 255), scale=2, mono_space=0)
    vers = 'V2.0 : https://www.loborobot.com'
    loading.draw_string(int(lcd.width()//2 - len(info) * 5), (lcd.height())//3 + 20, vers, color=(255, 255, 255), scale=1, mono_space=1)
    lcd.display(loading)
    del loading, info, vers
    gc.collect()
finally:
    gc.collect()
