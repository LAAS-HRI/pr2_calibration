#! /usr/bin/env python
# Software License Agreement (BSD License)
#
# Copyright (c) 2008, Willow Garage, Inc.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
#  * Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#  * Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#  * Neither the name of Willow Garage, Inc. nor the names of its
#    contributors may be used to endorse or promote products derived
#    from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
# FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
# COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
# INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

# author: Vijay Pradeep

import roslib; roslib.load_manifest('pr2_calibration_estimation')

import sys
import rospy
import time
import numpy
import rosrecord
import yaml
import os.path
import numpy

from numpy import matrix

from pr2_calibration_estimation.robot_params import RobotParams
from pr2_calibration_estimation.sensors.multi_sensor import MultiSensor
from pr2_calibration_estimation.opt_runner import opt_runner

def usage():
    rospy.logerr("Not enough arguments")
    print "Usage:"
    print " ./proto1.py [bagfile] [output_dir]"
    sys.exit(0)


def build_sensor_defs(sensors_dump):
    '''
    Given a list of sensor definition dictionaries, merge them into a single dictionary
    '''
    # There could be multiple defitions of sensors in the sensors namespace. We
    # need to merge all of these into a single consistent dictionary
    all_sensors_dict = dict()

    for cur_sensors_file in sensors_dump:
        for cur_sensor_type, cur_sensor_list in cur_sensors_file.items():
            for cur_sensor in cur_sensor_list:
                # We want sensor_ids to be unique. Thus, we should warn the user if there are duplicate block IDs being loaded
                if cur_sensor['sensor_id'] in all_sensors_dict.keys():
                    rospy.logwarn("Loading a duplicate [%s]. Overwriting previous" % cur_sensor['sensor_id'])
                all_sensors_dict[cur_sensor['sensor_id']] = cur_sensor
                all_sensors_dict[cur_sensor['sensor_id']]['sensor_type'] = cur_sensor_type

    print "The following error sensors have been loaded:\n"
    # We want to sort by the types of blocks
    all_sensor_types = list(set([x['sensor_type'] for x in all_sensors_dict.values()]))

    for cur_sensor_type in all_sensor_types:
        print "  %s sensors" % cur_sensor_type
        cur_sensor_ids = [cur_sensor_id for cur_sensor_id,cur_sensor in all_sensors_dict.items() if cur_sensor['sensor_type'] == cur_sensor_type]
        cur_sensor_ids.sort()
        for cur_sensor_id in cur_sensor_ids:
            print "   - %s" % cur_sensor_id
        print ""

    return all_sensors_dict

def load_calibration_steps(steps_dict):
    # We want to execute the calibration in alphabetical order, based on the key names
    step_keys = steps_dict.keys()
    step_keys.sort()
    step_list = []
    for step_name in step_keys:
        # Add the step name to the dict (since we'll lose this info once we listify)
        steps_dict[step_name]["name"] = step_name
        step_list.append(steps_dict[step_name])
    print "Loaded the calibration steps to execute in the following order:"
    for cur_step in step_list:
        print " - %s" % cur_step['name']
    return step_list


def load_requested_sensors(all_sensors_dict, requested_sensors):
    '''
    Build a sensor dictionary with the subset of sensors that we request
    '''
    all_sensor_types = list(set([x['sensor_type'] for x in all_sensors_dict.values()]))
    cur_sensors = dict([(x,[]) for x in all_sensor_types])
    for requested_sensor_id in requested_sensors:
        # We need to now find requested_sensor_id in our library of sensors
        if requested_sensor_id in all_sensors_dict.keys():
            cur_sensor_type = all_sensors_dict[requested_sensor_id]['sensor_type']
            cur_sensors[cur_sensor_type].append(all_sensors_dict[requested_sensor_id])
        else:
            rospy.logerr("Could not find [%s] in block library. Skipping this block")
    return cur_sensors

if __name__ == '__main__':
    rospy.init_node("multi_step_cov_estimator")

    print "Starting The Multi Step [Covariance] Estimator Node\n"

    if (len(rospy.myargv()) < 2):
        usage()
    elif (len(rospy.myargv()) < 3):
        bag_filename = rospy.myargv()[1]
        output_dir = "."
    else:
        bag_filename = rospy.myargv()[1]
        output_dir = rospy.myargv()[2]

    print "Using Bagfile: %s\n" % bag_filename
    if not os.path.isfile(bag_filename):
        rospy.logerr("Bagfile does not exist. Exiting...")
        sys.exit(1)

    config_param_name = "calibration_config"
    if not rospy.has_param(config_param_name):
        rospy.logerr("Could not find parameter [%s]. Please populate this namespace with the estimation configuration.", config_param_name)
        sys.exit(1)
    config = rospy.get_param(config_param_name)

    # Process all the sensor definitions that are on the parameter server
    sensors_name = "sensors"
    if sensors_name not in config.keys():
        rospy.logerr("Could not find namespace [%s/%s]. Please populate this namespace with sensors.", (config_param_name, sensors_name))
        sys.exit(1)
    sensors_dump = [yaml.load(x) for x in config[sensors_name].values()]
    all_sensors_dict = build_sensor_defs(sensors_dump)
    all_sensor_types = list(set([x['sensor_type'] for x in all_sensors_dict.values()]))

    # Load all the calibration steps.
    step_list = load_calibration_steps(config["cal_steps"])

    # Count how many checkerboard poses we need to track
    msg_count = 0
    f = open(bag_filename)
    multisensors = []
    for topic, msg, t in rosrecord.logplayer(f):
        if topic == "robot_measurement":
            msg_count+=1
    f.close()

    if 'initial_poses' in config.keys():
        previous_pose_guesses = numpy.array(yaml.load(config['initial_poses']))
    else:
        previous_pose_guesses = numpy.zeros([msg_count,6])

    # Specify which system the first calibration step should use.
    # Normally this would be set at the end of the calibration loop, but for the first step,
    # this is grabbed from the param server
    previous_system = yaml.load(config["initial_system"])

    # Load all the sensors from the bagfile and config file
    for cur_step in step_list:
        print ""
        print "*"*30
        print "Beginning [%s]" % cur_step["name"]

        # Need to load only the sensors that we're interested in
        cur_sensors = load_requested_sensors(all_sensors_dict, cur_step['sensors'])

        # Load all the sensors from bag
        f = open(bag_filename)
        multisensors = []
        for topic, msg, t in rosrecord.logplayer(f):
            if topic == "robot_measurement":
                # Hack to rename laser id
                for cur_laser in msg.M_laser:
                    if cur_laser.laser_id == "tilt_laser_6x8":
                        cur_laser.laser_id = "tilt_laser"
                ms = MultiSensor(cur_sensors)
                ms.sensors_from_message(msg)
                multisensors.append(ms)
        f.close()

        # Display sensor count statistics
        print "Executing step with the following Sensors:"
        # Iterate over sensor definitions for this step
        for cur_sensor_type, cur_sensor_list in cur_sensors.items():
            print "  %s Sensors:" % cur_sensor_type
            cur_sensor_ids = [cur_sensor['sensor_id'] for cur_sensor in cur_sensor_list]
            cur_sensor_ids.sort()
            for cur_sensor_id in cur_sensor_ids:
                counts = [ len([s for s in ms.sensors if s.sensor_id == cur_sensor_id]) for ms in multisensors]
                count = sum(counts)
                print "   - %s (%u)" % (cur_sensor_id, count)
            print ""

        print "Sensor breakdown (By Sample):"
        for k,ms in zip(range(len(multisensors)), multisensors):
            print " % 2u) %s" % (k, ", ".join([s.sensor_id for s in ms.sensors]))

        print "Pose Guesses:\n", previous_pose_guesses

        if len(multisensors) == 0:
            rospy.logwarn("No error blocks were generated for this optimization step. Skipping this step.  This will result in a miscalibrated sensor")
            output_dict = previous_system
            output_poses = previous_pose_guesses
        else:
            free_dict = yaml.load(cur_step["free_params"])
            output_dict, output_poses, J = opt_runner(previous_system, previous_pose_guesses, free_dict, multisensors)

        # Dump results to file
        out_f = open(output_dir + "/" + cur_step["output_filename"] + ".yaml", 'w')
        yaml.dump(output_dict, out_f)
        out_f.close()

        out_f = open(output_dir + "/" + cur_step["output_filename"] + "_poses.yaml", 'w')
        yaml.dump([list([float(x) for x in pose]) for pose in list(output_poses)], out_f)
        out_f.close()

        cov_x = matrix(J).T * matrix(J)
        numpy.savetxt(output_dir + "/" + cur_step["output_filename"] + "_cov.txt", cov_x, fmt="% 9.3f")

        previous_system = output_dict
        previous_pose_guesses = output_poses
