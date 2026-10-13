# M2 Position and motion -> decision core: one position source and its error.
std_msgs/Header header  # stamp: when it was measured; frame_id: the map frame
float32 trust           # 0.0 (ignore) to 1.0 (fully trusted)

uint8 GPS=0
uint8 LIDAR_MAP=1
uint8 ODOMETRY=2
uint8 source

geometry_msgs/Point position
float32 error_m         # estimated horizontal error
