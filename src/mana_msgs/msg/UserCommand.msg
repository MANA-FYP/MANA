# M4 Voice or M5 Interface -> decision core: one command from the user.
std_msgs/Header header  # stamp: when it was given
float32 trust           # recognition confidence; 1.0 for touch and buttons

uint8 VOICE=0
uint8 TOUCH=1
uint8 BUTTON=2
uint8 source

uint8 STOP=0            # highest priority
uint8 GO=1
uint8 LEFT=2
uint8 RIGHT=3
uint8 SPEED_UP=4        # voice "up"
uint8 SLOW_DOWN=5       # voice "down"
uint8 YES=6
uint8 NO=7
uint8 AUTOMATIC=8       # voice "on"
uint8 MANUAL=9          # voice "off"
uint8 WHEELS=10         # touch and buttons only
uint8 TRACKS=11         # touch and buttons only
uint8 command
