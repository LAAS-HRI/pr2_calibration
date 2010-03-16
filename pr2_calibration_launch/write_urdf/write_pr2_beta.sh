#! /bin/bash

original_system=`rospack find pr2_calibration_launch`/estimate_params/config_pr2_beta/cov/system.yaml
final_system=/tmp/pr2_calibration/system_calibrated.yaml
urdf_in=/etc/ros/urdf/robot.xml
urdf_out=robot_calibrated.xml
`rospack find pr2_calibration_propagation`/scripts/propagate_config_ref_positions.py $original_system $final_system $urdf_in $urdf_out
echo "Wrote new URDF to $urdf_out"