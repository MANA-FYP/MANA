# M1 Vision -> decision core: the nearest obstacle in the path, as seen by one source.
std_msgs/Header header  # stamp: when it was measured; frame_id: the sensor frame
float32 trust           # 0.0 (ignore) to 1.0 (fully trusted)

uint8 CAMERA=0
uint8 DEPTH_CAMERA=1
uint8 LIDAR=2
uint8 source

string label            # detector class, for example "person"; empty when the type is unknown
float32 distance_m
float32 bearing_rad     # angle from straight ahead, positive to the left (REP 103)
