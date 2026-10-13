# M1 Vision -> decision core: the ground ahead, as seen by one source.
# Every module report starts with the same two fields: when it was measured, and how far to trust it.
std_msgs/Header header  # stamp: when it was measured; frame_id: the sensor frame
float32 trust           # 0.0 (ignore) to 1.0 (fully trusted)

uint8 CAMERA=0
uint8 DEPTH_CAMERA=1
uint8 source

uint8 FLAT=0
uint8 RAMP=1
uint8 STAIRS_UP=2
uint8 STAIRS_DOWN=3
uint8 CURB=4
uint8 DROP=5
uint8 UNKNOWN=255
uint8 ground

float32 distance_m      # distance to where the ground changes; NaN when it does not change
float32 step_height_m   # height of the step or drop; NaN when not measured
