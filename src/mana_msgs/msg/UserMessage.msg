# Decision core -> M4 Voice and M5 Interface: one message for the user.
std_msgs/Header header

uint8 INFO=0
uint8 WARNING=1
uint8 URGENT=2           # the host interrupts what it is saying and says this now
uint8 priority          # the most urgent message goes first

string text             # for example "Stairs ahead, changing to tracks"
bool speak              # also say it aloud, not only on the screen
