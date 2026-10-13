# M2 Position and motion -> decision core: tilt and wheel slip.
std_msgs/Header header  # stamp: when it was measured; frame_id: the base frame
float32 trust           # 0.0 (ignore) to 1.0 (fully trusted)

float32 pitch_rad       # REP 103 axes
float32 roll_rad
bool wheel_slip         # the wheel encoders and the IMU disagree
