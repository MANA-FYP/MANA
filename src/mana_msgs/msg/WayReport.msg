# 3D camera -> decision core: which way is free, in three corridors as wide as the device: to the
# left, ahead and to the right (mana_perception.structure.way). The core tells the user which way
# is open when the way ahead is blocked ("Way ahead blocked. Open to your right.").
std_msgs/Header header  # stamp: when it was measured; frame_id: the sensor frame
float32 trust           # 0.0 (ignore) to 1.0 (fully trusted)
float32 left_m          # to the nearest thing blocking each corridor: inf when nothing does,
float32 ahead_m         # NaN when too little of it is seen to tell
float32 right_m
