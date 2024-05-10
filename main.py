from machine import Pin, I2C, ADC
from ssd1306 import SSD1306_I2C
from fifo import Fifo
import utime
import micropython
import math
import network
import socket
import urequests as requests
import ujson
import gc

import network
from umqtt.simple import MQTTClient
import time

micropython.alloc_emergency_exception_buf(200)
delay = 0.002 ## seconds
rot_button_up = True

# oled set up

OLED_SDA = 14
OLED_SCL = 15
i2c = I2C(1, scl=Pin(OLED_SCL), sda=Pin(OLED_SDA), freq=400000)
OLED_WIDTH = 128
OLED_HEIGHT = 64
oled = SSD1306_I2C(OLED_WIDTH, OLED_HEIGHT, i2c)
line_height = 10

# visual variables and functions
xpos = 5
background_color = 0
text_color = 1
oled.fill(background_color)

# some global variables
empty_peaklist_template = [{"value": None,
             "ticks_literal": utime.ticks_ms(),
             "ticks_counted": None}]

peaklist = empty_peaklist_template

history = []
last_three_bpms = []
not_too_much_variety = False

# Kubios credentials
APIKEY = "pbZRUi49X48I56oL1Lq8y8NDjq6rPfzX3AQeNo3a"
CLIENT_ID = "3pjgjdmamlj759te85icf0lucv"
CLIENT_SECRET = "111fqsli1eo7mejcrlffbklvftcnfl4keoadrdv1o45vt9pndlef"

LOGIN_URL = "https://kubioscloud.auth.eu-west-1.amazoncognito.com/login"
TOKEN_URL = "https://kubioscloud.auth.eu-west-1.amazoncognito.com/oauth2/token"
REDIRECT_URI = "https://analysis.kubioscloud.com/v1/portal/login"

# connection to wireless network
SSID = "KMD759_Group_3"
PASSWORD = "ryhmaC1234"
BROKER_IP = "192.168.3.253"


def publish_message(message, BROKER="192.168.3.253", TOPIC="topic/test", PORT=1883):
    try:
        client = MQTTClient("pico_client", BROKER, PORT)
        client.connect()
        client.publish(TOPIC, message)
        print("Published:", message)
        client.disconnect()
    except Exception as e:
        pass
        
def connect_wlan():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    wlan.connect(SSID, PASSWORD)
    info = wlan.ifconfig()
    while not wlan.isconnected():
        publish_message("Connecting...")
        oled.fill(0)  
        oled.text("Connecting...", 1, 10)
        oled.show()
        utime.sleep(1)
        
    publish_message(f"Connection successful. Pico IP: {wlan.ifconfig()[0]}")
    
    oled.fill(0) 
    oled.text("Connected", 1, 10) 
    oled.text(str(info[0]), 5, 30, 1)
    oled.show()
    utime.sleep(1)  
    oled.fill(0) 

# Using RESTFUL and posting that uses HTTP requestes to access and use data.
def kubios_send(intervals):
    gc.collect()
    pure_interval_list = []
    for i in intervals:
        if i["ticks_counted"] != None:
            pure_interval_list.append(i["ticks_counted"])
        
    response = requests.post(
        url=TOKEN_URL,
        data='grant_type=client_credentials&client_id={}'.format(CLIENT_ID),
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        auth=(CLIENT_ID, CLIENT_SECRET)
    )
    response = response.json()
    access_token = response["access_token"]

    data_set = {
        "type": "RRI",
        "data": pure_interval_list,
        "analysis": {
            "type": "readiness"
        }
    }


    response = requests.post(
        url="https://analysis.kubioscloud.com/v2/analytics/analyze",
        headers={
            "Authorization": "Bearer {}".format(access_token),
            "X-Api-Key": APIKEY
        },
        json=data_set
    )

    response = response.json()
    publish_message(f"KUBIOS Responded: {response}")
        
    pns_index = response['analysis']['pns_index']
    sns_index = response['analysis']['sns_index']
    print("SNS value_Kubios: ", sns_index)
    print("PNS value_kubios: ", pns_index)
    publish_message(f"KUBIOS SNS Index: {sns_index}")
    publish_message(f"KUBIOS PNS Index: {pns_index}")

    sdnn_kubios_ms = response['analysis']['sdnn_ms']
    rmssd_kubios_ms = response['analysis']['rmssd_ms']
    local_calc = calculate_hrv(intervals)
    mean_ppi = local_calc['meanPPI']
    mean_hr = local_calc['meanHR']
    sdnn = local_calc['sdnn']
    rmssd = local_calc['rmssd']
    
    results = {"meanPPI": mean_ppi, "meanHR": mean_hr, "SDNN": sdnn_kubios_ms, "RMSSD": rmssd_kubios_ms, "SNS": sns_index, "PNS": pns_index}

    # Get the current time as a tuple
    current_time = utime.localtime()

    # Format and print the current time
    formatted_time = f"{current_time[2]:02d}-{current_time[1]:02d}-{current_time[0] % 100:02d} {current_time[3]:02d}:{current_time[4]:02d}"

    kubios_history = load_history()
    kubios_history.append({"results": results, "timestamp": formatted_time})
    save_history(kubios_history)
    publish_message(f"History entry saved: {kubios_history[-1]}")

    return results

# history functions

# Function to load history from a JSON file
def load_history():
    try:
        with open("history.json", 'r') as file:
            kubios_history = ujson.load(file)
        return kubios_history
    except (OSError, ValueError):
        return []

# Function to save history to a JSON file
def save_history(history):
    with open("history.json", 'w') as file:
        ujson.dump(history, file)

pixel_position = 0

def put_pixel(value, minim, maxim):
    global pixel_position
    if pixel_position == OLED_WIDTH:
        pixel_position = 0
        oled.fill_rect(0, 30, OLED_WIDTH, 10, 0)
    else:
        pixel_position += 1

    try:
        scaled_value = ((value - minim) / (maxim - minim)) * (38 - 30) + 30
        oled.pixel(int(pixel_position), int(scaled_value), 1)
    except ZeroDivisionError:
        oled.pixel(int(pixel_position), 30, 1)


# calculation functions
def heart_rate_detection(value, history_size=1000, tick_diff_min=400):
    global history, peaklist, slot1, slot2, slot3, last_three_bpms, not_too_much_variety, pixel_position
    
    history.append(value)
    
    history = history[-history_size:]
    minim = min(history)
    maxim = max(history)
    literal_tick_count = utime.ticks_ms()

    threshold_on = (minim + maxim * 3) // 4   # 3/4
    threshold_off = (minim + maxim) // 2      # 1/2
    
    if value > threshold_on:
        counted_tick_count = literal_tick_count - peaklist[-1]["ticks_literal"]
        if counted_tick_count > tick_diff_min:
            peaklist.append({"value": value,
                             "ticks_literal": utime.ticks_ms(),
                             "ticks_counted": utime.ticks_ms() - peaklist[-1]["ticks_literal"]})

            BPM = 60000 / peaklist[-1]["ticks_counted"]
            last_three_bpms.append(BPM)
            

            if len(last_three_bpms) >= 3:
                last_three_bpms = last_three_bpms[-3:]                
                not_too_much_variety = BPM - (sum(last_three_bpms) / len(last_three_bpms)) < 20
            
            if 40 < BPM < 250:
                print("BPM:", BPM)
                oled.fill_rect(xpos, line_height, OLED_WIDTH, line_height * 2, background_color)
                oled.text("<3", xpos, line_height * 2, text_color)
                if not_too_much_variety:
                    counted_bpm = (sum(last_three_bpms) / len(last_three_bpms))
                    oled.text(f"HR: {counted_bpm:.1f} BPM", xpos, line_height, text_color)
                    publish_message(f"HR: {counted_bpm:.1f}")
                else:
                    oled.text("bad signal...", xpos, line_height, text_color)
                
        peaklist[-history_size:]
    
    if (literal_tick_count - peaklist[0]["ticks_literal"]) < 12000:
        oled.fill_rect(xpos, 0, OLED_WIDTH, line_height * 2, background_color)
        oled.text("calculating...", xpos, line_height, text_color)
        
        
    if value < threshold_off:
        oled.text("<3", xpos, line_height * 2, background_color)
        
    put_pixel(value, minim, maxim)
    oled.text("back to menu ->", xpos, line_height * 5, text_color)
    oled.show()
    
def get_peaks(peaks_to_collect=20, history_size=1000, tick_diff_min=400):
    global history, peaklist
    del peaklist
    peaklist = [{"value": None,
             "ticks_literal": utime.ticks_ms(),
             "ticks_counted": None}]
   
    print("Collecting peaks")

       
    while peaks_to_collect > len(peaklist):
        oled.fill(background_color)
        oled.text("Collecting...", xpos, line_height, text_color)
        oled.text("Keep a finger", xpos, line_height * 2, text_color)
        oled.text("on the sensor", xpos, line_height * 3, text_color)
        
 
        value = adc.read_u16()
        history.append(value)
        history = history[-history_size:]
        minim = min(history)
        maxim = max(history)
        literal_tick_count = utime.ticks_ms()

        threshold_on = (minim + maxim * 3) // 4   # 3/4
        threshold_off = (minim + maxim) // 2      # 1/2
        
        if value > threshold_on:
            counted_tick_count = literal_tick_count - peaklist[-1]["ticks_literal"]
            if counted_tick_count > tick_diff_min:
                peaklist.append({"value": value,
                                 "ticks_literal": utime.ticks_ms(),
                                 "ticks_counted": utime.ticks_ms() - peaklist[-1]["ticks_literal"]})
                oled.text(f"{len(peaklist)} / {peaks_to_collect}", xpos, line_height * 5, text_color) 
                oled.show()
    
    return peaklist
                
phase = 1

def local_hrv_reading(amount_to_read=20):
    global peaklist, phase
    adc_value = adc.read_u16()
    oled.text("collecting data", xpos, line_height * 2, text_color)
    oled.text("back to menu ->", xpos, line_height * 5, text_color)
    
    if phase == 1:
        local_peaklist = get_peaks(amount_to_read)        
        print("OK", len(local_peaklist))
        phase = 2
    elif phase == 2:
        calc = calculate_hrv(peaklist)

        oled.fill(0)
        oled.text(f"MEAN HR {calc['meanHR']} BPM", 2, line_height, text_color)
        oled.text(f"MEAN PPI {calc['meanPPI']} MS", 2, line_height * 2, text_color)
        oled.text(f"SDNN {calc['sdnn']}", 2, line_height * 3, text_color)
        oled.text(f"RMSSD {calc['rmssd']}", 2, line_height * 4, text_color)
        oled.text("back to menu ->", 2, line_height * 5, text_color)
        oled.show()
        print("DONE")
        phase = 3
    else:
        return
    
def calculate_hrv(peaklist):
    hrsum = 0
    ppisum = 0
    sqdiffsum = 0
    sqdiffsuccessive = 0
    amount_of_valid = 0
    
    for i in range(1, len(peaklist)-1):
        if peaklist[i]["ticks_counted"] < 5000:
            amount_of_valid += 1
            hrsum += 60000 / peaklist[i]["ticks_counted"]
            ppisum += peaklist[i]["ticks_counted"]
        
    meanHR = int(hrsum / len(peaklist))
    meanPPI = int(ppisum / len(peaklist))
    
    for i in range(1, len(peaklist)-2):
        sqdiffsum += peaklist[i]["ticks_counted"] - meanPPI
        sqdiffsuccessive += (peaklist[i]["ticks_counted"] - peaklist[i+1]["ticks_counted"])
        
    sdnn = math.sqrt((sqdiffsum ** 2) / (len(peaklist) - 1))
    rmssd = math.sqrt((sqdiffsuccessive ** 2) / (len(peaklist) - 2))
    
    return {'meanHR': meanHR, 'meanPPI': meanPPI, 'sdnn': sdnn, 'rmssd': rmssd}
        
    
def check_back_to_menu():
    global rot_button_up, device_mode
    if not rot_button_up:
        utime.sleep_ms(10)
        rot_button_up = True
        oled.fill(background_color)
        device_mode = "menu"
        gc.collect()
        menu.draw_menu()
        cur.move(0)
        print("rotary encoder button pressed")


def make_rect(row, color, show=True):
    oled.fill_rect(xpos, line_height*row, OLED_WIDTH, line_height * 2, color)
    if show:
        oled.show()
        
def display_text(text, x, y, color=0, fill_screen=True, show=True, delay=125):
    ## where color refers to text color
    ## Utility function to display text on OLED.
    if fill_screen:
        if color == 0:
            oled.fill(1)
        if color == 1:
            oled.fill(0)
    oled.text(text, x, y, color)
    if show:
        oled.show()
    utime.sleep_ms(delay)

def animate_welcome_text(base_text, x, y):
    for i in range(1, len(base_text)+1):
        display_text(base_text[:i], x, y, delay=125 if i < len(base_text) else 1000)

def welcome_text_2():
    oled.fill(1)
    display_text("PulsePro", 26, 17, fill_screen=False, delay=800)
    display_text("Developed by:", 15, 27, fill_screen=False, delay=800)
    display_text("Team C", 36, 47, fill_screen=False, delay=1500)
    
 
# pin classes
class RotaryEncoder:
    def __init__(self, rot_a, rot_b):
        self.a = Pin(10, mode = Pin.IN, pull = Pin.PULL_UP)
        self.b = Pin(11, mode = Pin.IN, pull = Pin.PULL_UP)
        self.fifo = Fifo(30, typecode = "i")
        self.a.irq(handler = self.handler, trigger = Pin.IRQ_RISING, hard = True)
        
    def handler(self, pin):
        if self.b():
            self.fifo.put(-1)
        else:
            self.fifo.put(1)

            
class RotButton:
    def __init__(self, this_pin):
        self.pin = Pin(this_pin, mode = Pin.IN, pull = Pin.PULL_UP)
        self.pin.irq(handler = self.handler, trigger = Pin.IRQ_RISING, hard = True)
        
    def handler(self, pin):
        global rot_button_up
        rot_button_up = not rot_button_up

class Menu:
    def __init__(self, contentlist, xpos=10, ypos=5):
        self.contentlist = contentlist
        self.xpos = xpos
        self.ypos = ypos
    
    def draw_menu(self):
        contents = self.contentlist
        line = 0
        for i in contents:
            oled.text(i, self.xpos, line_height * line + self.ypos, text_color)
            oled.show()
            line += 1
            print(i, "yes")
    
    def choose(self, position):
        oled.fill(background_color)
        # converts contentlist dictionary to a list of keys and uses the [position] to access it
        func = self.contentlist[list(self.contentlist)[position]]
        # executes the retrieved function
        func()
    
    def choose_history(self, position): # different scenario
        oled.fill(background_color)
        contents = self.contentlist[list(self.contentlist)[position]]["results"]
        counter = 0
        for key, value in contents.items():
            print(counter, key, value)
            oled.text(f"{key}: {value:.2f}", 1, line_height * counter, text_color)
            counter += 1
        oled.show()
        
class Cursor:
    def __init__(self, graphic=">", xpos=0, ypos=5, position=0):
        self.graphic = graphic
        self.xpos = xpos
        self.ypos = ypos
        self.position = 0
    
    def move(self, new_position):
        global line_height, background_color, text_color
        oled.text(self.graphic, self.xpos, self.position * line_height + self.ypos, background_color)
        self.position = new_position
        oled.text(self.graphic, self.xpos, new_position * line_height + self.ypos, text_color)
        oled.show()
        
def HR_measure():
    print("ENTERING BASIC HR MEASUREMENT")
    global device_mode
    device_mode = "HR_measure"

def HRV_analyse():
    print("ENTERING BASIC LOCAL HRV MEASUREMENT")
    global device_mode
    device_mode = "HRV_local"
    
def HRV_analyse_kubios():
    print("ENTERING KUBIOS MEASUREMENT")
    global device_mode
    device_mode = "kubios_analyse"
    
menu_drawn = False

def display_history():
    print("ENTERING HISTORY")
    global device_mode
    menu_drawn = False
    device_mode = "display_history"
    
def toggle_dark_mode():
    print("ENTERING DARK/LIGHT MODE TOGGLE")
    global device_mode
    device_mode = "toggle_dark_mode"
    

# fifo set up and data collecting functions
samplefifo = Fifo(32)
samplerate = 0.04 ## in seconds, 0.04 = 250 Hz


menu_list = {"Measure HR": HR_measure,
             "HRV Analysis": HRV_analyse,
             "KUBIOS": HRV_analyse_kubios,
             "History": display_history,
             "Toggle Theme": toggle_dark_mode}

menu = Menu(menu_list)
cur = Cursor()

rot = RotaryEncoder(10, 11)
rot_button = RotButton(12)
position = 0

adc = ADC(26)


device_mode = "start_screen"
if device_mode == "start_screen":
    animate_welcome_text("PulsePro", 26, 17)
    welcome_text_2()
    oled.fill(background_color)
    connect_wlan()
#    publish_message("Connected")
    oled.fill(background_color)
    menu.draw_menu()
    cur.move(0)
    device_mode = "menu"
    


while True:
    
    if device_mode == "menu":
        if not rot_button_up:
            utime.sleep_ms(10)
            rot_button_up = True
            print("pressed at position", position)
            menu.choose(position)
        if rot.fifo.has_data():
            dir = rot.fifo.get()
            if dir == 1:
                if position < (len(menu_list) - 1):
                    position += 1
                else:
                    position = (len(menu_list) - 1)
            if dir == -1:
                if position > 0:
                    position -= 1
                else:
                    position = 0
            cur.move(position)
            
    if device_mode == "HR_measure":
        value = adc.read_u16()
        heart_rate_detection(value)
        
        check_back_to_menu()
        
    if device_mode == "HRV_local":
        local_hrv_reading()
        check_back_to_menu()

    if device_mode == "toggle_dark_mode":
        background_color = not background_color
        text_color = not text_color
        oled.fill(background_color)
        
        if text_color == 1:
            text = " Dark Mode"
        else:
            text = " Light Mode"
            
        oled.text(text, 5, line_height * 3, text_color)
        oled.show()
        utime.sleep(2)
        oled.fill(background_color)
        menu.draw_menu()
        cur.move(0)
        device_mode = "menu"
        
    if device_mode == "kubios_analyse":

        peaklist = empty_peaklist_template
        get_peaks(40)
        oled.fill(background_color)
        oled.text("Sending data to", xpos, line_height, text_color)
        oled.text("Kubios Cloud...", xpos, line_height * 2, text_color)
        oled.show()
        
        try:
            results = kubios_send(peaklist)
            # returns {"meanPPI": mean_ppi, "meanHR": mean_hr, "SDNN": sdnsdnn_kubios_ms, "RMSSD": rmssd_kubios_ms, "SNS": sns_index, "PNS": pns_index}
            device_mode = "kubios_show"
        
        except OSError as e:
            if e.args == errno.ENOMEM:
                oled.text("Memory failed,", xpos, line_height, text_color)
                oled.text("return to menu", xpos, line_height * 2, text_color)
                oled.show()
            else:
                oled.text("OSError occurred", xpos, line_height, text_color)
                oled.show()
            utime.sleep(2)
            oled.fill(background_color)
            device_mode = "menu"
                
        
    if device_mode == "kubios_show":
        oled.fill(background_color)
        oled.text(f"MEAN HR: {results['meanHR']}", xpos, 2, text_color)
        oled.text(f"MEAN PPI: {results['meanPPI']}", xpos, line_height + 2, text_color)
        oled.text(f"SDNN: {results['SDNN']}", xpos, line_height * 2 + 2, text_color)
        oled.text(f"RMSSD: {results['SDNN']}", xpos, line_height * 3 + 2, text_color)
        oled.text(f"SNS: {results['SNS']}", xpos, line_height * 4 + 2, text_color)
        oled.text(f"PNS: {results['PNS']}", xpos, line_height * 5 + 2, text_color)        
        oled.show()
        
        
        device_mode = "static"
    
    if device_mode == "display_history":
        if menu_drawn == False:
            kubios_history = load_history()
            kubios_menu = {}
            for i in kubios_history:
                kubios_menu[f"{i['timestamp']}"] = i
            if len(kubios_menu) == 0:
                oled.text("No KUBIOS", xpos, line_height, text_color)
                oled.text("entries", xpos, line_height * 2, text_color)    
                oled.show()
                utime.sleep(2)
                oled.fill(background_color)
                menu.draw_menu()
                cur.move(3)
                device_mode = "menu"
            else:
                kubios_menu_obj = Menu(kubios_menu) 
                kubios_menu_obj.draw_menu()
                menu_drawn = True
               
        if menu_drawn:
            if not rot_button_up:
                utime.sleep_ms(10)
                rot_button_up = True
                kubios_menu_obj.choose_history(position)
                oled.fill(background_color)
                device_mode = "static"
            if rot.fifo.has_data():
                dir = rot.fifo.get()
                if dir == 1:
                    if position < (len(kubios_menu) - 1):
                        position += 1
                    else:
                        position = (len(kubios_menu) - 1)
                if dir == -1:
                    if position > 0:
                        position -= 1
                    else:
                        position = 0
                cur.move(position)
        
    if device_mode == "static":        
        check_back_to_menu()
        
    utime.sleep_ms(10)
