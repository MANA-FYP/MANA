# M3 Connectivity -> decision core: the quality of one network link.
std_msgs/Header header  # stamp: when it was measured
float32 trust           # 0.0 (ignore) to 1.0 (fully trusted)

uint8 WIFI=0
uint8 BLUETOOTH=1
uint8 link

bool connected
float32 rssi_dbm          # signal strength, for example -75.0
uint8 missed_heartbeats   # in a row
