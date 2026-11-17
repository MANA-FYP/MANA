# Wi-Fi/Bluetooth -> decision core: whether a person is near, sensed without a camera (#194).
# Wi-Fi sensing gives the room or a zone of it, not a distance, so this never stops the device:
# the core only slows down while no other rule is acting.
std_msgs/Header header  # stamp: the end of the window it was measured over
float32 trust           # 0.0 (ignore) to 1.0 (fully trusted): the model's probability

uint8 WIFI_CSI=0          # the channel state of a Wi-Fi link (ESP32 boards)
uint8 source

bool present            # a person is in the sensed area
string zone             # where, when the model knows (for example "zone 1"); empty otherwise
