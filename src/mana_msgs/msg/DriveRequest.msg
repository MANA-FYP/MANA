# Decision core -> host: what the host's own controller should do.
std_msgs/Header header

uint8 WHEELS=0
uint8 TRACKS=1
uint8 mode

bool stop               # true overrides mode and speed
float32 speed_limit     # 0.0 to 1.0 of the host's top speed
string reason           # short, for the log and the screen, for example "stairs ahead"
