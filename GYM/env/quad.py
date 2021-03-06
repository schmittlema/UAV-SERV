import gym
import numpy as np
import os
import rospy
import roslaunch
import subprocess
import time
import math
from random import randint
import random

from gym import utils, spaces
import gazebo_env
from gym.utils import seeding

from mavros_msgs.msg import OverrideRCIn, PositionTarget,State
from sensor_msgs.msg import LaserScan, NavSatFix
from std_msgs.msg import Float64;
from gazebo_msgs.msg import ModelStates,ModelState
from gazebo_msgs.srv import SetModelState,GetModelState

from mavros_msgs.srv import CommandBool, CommandTOL, SetMode
from geometry_msgs.msg import PoseStamped,Pose,Vector3,Twist,TwistStamped
from std_srvs.srv import Empty
from VelocityController import VelocityController

#For Stereo
from sensor_msgs.msg import Image
import cv2
from cv_bridge import CvBridge, CvBridgeError
import matplotlib.pyplot as plt


class GazeboQuadEnv(gazebo_env.GazeboEnv):
    def _takeoff(self):
	print "Got Mavros"
        last_request = rospy.Time.now()
        while self.state.mode != "OFFBOARD" or not self.state.armed:
            if rospy.Time.now() - last_request > rospy.Duration.from_sec(5):
                self._reset()
                # Set OFFBOARD mode
                rospy.wait_for_service('mavros/set_mode')
                
                #Must be sending points before connecting to offboard
                for i in range(0,100):
                    self.local_pos.publish(self.pose)
                if self.state.mode != "OFFBOARD":
                    try:
                        success = self.mode_proxy(0,'OFFBOARD')
                        print success
                    except rospy.ServiceException, e:
                        print ("mavros/set_mode service call failed: %s"%e)
                    print "offboard enabled"

                if not self.state.armed:
                    print "arming"
                    rospy.wait_for_service('mavros/cmd/arming')
                    try:
                       success = self.arm_proxy(True)
                       print success
                    except rospy.ServiceException, e:
                       print ("mavros/set_mode service call failed: %s"%e)
                    print "armed"
                last_request = rospy.Time.now()
            self.local_pos.publish(self.pose)
            self.rate.sleep()

        timeout = 150
        err = 1
        while err > .3:
            err = abs(self.pose.pose.position.z - self.cur_pose.pose.position.z)
            self.local_pos.publish(self.pose)
            self.rate.sleep()
            timeout -= 1 
            if timeout < 0:
                self._reset()
                timeout = 150

        self.started = True
        print self.started
        print "Initialized"


    def _pause(self, msg):
        programPause = raw_input(str(msg))

    def __init__(self):
        # Launch the simulation with the given launchfile name
        gazebo_env.GazeboEnv.__init__(self, "mpsl.launch")    

        self.targetx = randint(-10,10)
        self.targety = 0
        self.max_distance = 1.6

        rospy.Subscriber('/mavros/local_position/pose', PoseStamped, callback=self.pos_cb)
        rospy.Subscriber('/mavros/local_position/velocity', TwistStamped, callback=self.vel_cb)

        rospy.Subscriber('/mavros/state',State,callback=self.state_cb)

        self.unpause = rospy.ServiceProxy('/gazebo/unpause_physics', Empty)

        self.pause = rospy.ServiceProxy('/gazebo/pause_physics', Empty)
	
	self.model_state = rospy.ServiceProxy('/gazebo/set_model_state', SetModelState)
	self.get_model_state = rospy.ServiceProxy('/gazebo/get_model_state', GetModelState)

        self.reset_proxy = rospy.ServiceProxy('/gazebo/reset_world', Empty)

	self.local_pos = rospy.Publisher('mavros/setpoint_position/local',PoseStamped,queue_size=10)

	self.local_vel = rospy.Publisher('mavros/setpoint_velocity/cmd_vel',TwistStamped,queue_size=10)

        self.mode_proxy = rospy.ServiceProxy('mavros/set_mode', SetMode)

        self.arm_proxy = rospy.ServiceProxy('mavros/cmd/arming', CommandBool)
        
        self.takeoff_proxy = rospy.ServiceProxy('mavros/cmd/takeoff', CommandTOL)
        self.bridge = CvBridge()

        rospy.Subscriber('uav_camera_down/image_raw_down',Image,self.callback)

        self.state = ""
        self.twist = TwistStamped()

        self.x_vel = 0
        self.y_vel = 0
	
	self.successes = 0


        self._seed()

        self.alt = 10

        self.cur_pose = PoseStamped()
	self.cur_vel = TwistStamped()

        self.pose = PoseStamped()
        self.pose.pose.position.x = 0
        self.pose.pose.position.y = 0
        self.pose.pose.position.z = self.alt

        self.hold_state = PoseStamped()
        self.hold_state.pose.position.x = 0
        self.hold_state.pose.position.y = 0

        self.started = False
        self.rate = rospy.Rate(10)

        self.pause_sim = False
        self.nowait = True
        self.new_pose = False
        self.steps = 0
        self.max_episode_length = 200
        self.img = False


    def pos_cb(self,msg):
        self.cur_pose = msg
        self.new_pose = True

    def vel_cb(self,msg):
        self.cur_vel = msg

    def state_cb(self,msg):
        self.state = msg

    def start(self):
	counter = 15
	while counter != 0:
	    counter = counter -1
 	    time.sleep(1)
        
        vController = VelocityController()
        vController.setTarget(self.pose.pose)

        self.get_data()
        self._takeoff()
        self.nowait = False

        print "Main Running"
        while not rospy.is_shutdown():
            des_vel = vController.update(self.cur_pose,self.x_vel,self.y_vel,self.hold_state)
            self.local_vel.publish(des_vel)
            self.rate.sleep()
            self.pauser()

    def pauser(self):
        if self.pause_sim and not rospy.is_shutdown():
            rospy.wait_for_service('/gazebo/pause_physics')
            try:
                self.pause()
            except rospy.ServiceException, e:
                print ("/gazebo/pause_physics service call failed")
            while self.pause_sim and not rospy.is_shutdown():
                self.rate.sleep()

            rospy.wait_for_service('/gazebo/unpause_physics')
            try:
                self.unpause()
            except rospy.ServiceException, e:
                print ("/gazebo/unpause_physics service call failed")

    def callback(self,data):
        try:
            self.img = self.bridge.imgmsg_to_cv2(data,"bgr8")
        except CvBridgeError as e:
            print(e)

    def observe(self):
        return self.img
    
    def wait_until_start(self):
        while True:
            if self.started:
                break
            self.rate.sleep()
        return

    def get_data(self):
        print "Waiting for mavros..."
        data = None
        while data is None:
            try:
                data = rospy.wait_for_message('/mavros/global_position/rel_alt', Float64, timeout=5)
            except:
                pass

    def reset_model(self,height):
	modelstate = ModelState()
	modelstate.model_name = "f450"
	modelstate.pose.position.x = 0
	modelstate.pose.position.y = 0
	modelstate.pose.position.z = height
	self.model_state(modelstate)

    def move_apriltag(self):
	modelstate = ModelState()
	modelstate.model_name = "apriltag"
        numbers = range(-3,-1) + range(1,3)
	modelstate.pose.position.x = random.choice(numbers)
        numbers = range(-3,-1) + range(1,3)
	modelstate.pose.position.y = random.choice(numbers)
	modelstate.pose.position.z = 0
	self.model_state(modelstate)

    def _seed(self, seed=None):
        self.np_random, seed = seeding.np_random(seed)
        return [seed]

    def detect_crash(self):
        if self.cur_pose.pose.position.z < 0.3:
            return True
        return False

    def get_state(self):
        return self.get_model_state("f450","world")

    def get_april_state(self):
        return self.get_model_state("apriltag","world")

    def wait_for_reposition(self,x,y,z):
        self.x_vel = 0
        self.y_vel = 0
        while abs(self.cur_pose.pose.position.z - z) > .3 or abs(self.cur_pose.pose.position.y - y) > .3 or abs(self.cur_pose.pose.position.x - x) > .3 or abs(self.get_state().pose.position.z - z) > .3 or abs(self.get_state().pose.position.y - y) > .3 or abs(self.get_state().pose.position.x - x) > .3:
            self.rate.sleep()

    def over_tag(self):
        return abs(self.get_state().pose.position.y -self.get_april_state().pose.position.y) < .3 and abs(self.get_state().pose.position.x -self.get_april_state().pose.position.x) < .3

    def detect_done(self,reward):
        done = False
        if self.detect_crash():
	    print "CRASH"
            done = True
            self.steps = 0
	    self.hard_reset()
        if self.over_tag():
	    print "GOAL"
            done = True
            self.steps = 0
	    self.successes += 1
	    self.reset_model(self.alt)
            self.wait_for_reposition(0,0,self.alt)
        if self.steps >= self.max_episode_length:
	    print "MAXOUT"
            done = True
            self.steps = 0
	    self.reset_model(self.alt)
            self.wait_for_reposition(0,0,self.alt)
	    reward = reward -2
        return done,reward

    def _step(self, action):
        self.steps += 1
        self.pause_sim = 0 
        if action == 0: #HOLD
            self.x_vel = 0
            self.y_vel = 0
        elif action == 1: #LEFT
            self.x_vel = -0.5
        elif action == 2: #RIGHT
            self.x_vel = 0.5
        elif action == 3: #FORWARD
            self.y_vel = 0.5
        elif action == 4: #BACK
            self.y_vel = -0.5

        self.rate.sleep()
        observation = self.observe()
        reward = self.get_reward(action)

        done,reward = self.detect_done(reward) 

        if done:
            self.move_apriltag()
        self.pause_sim = 1
        return observation, reward,done,{}

    def get_reward(self,action):
	rawx = self.get_state().pose.position.x-self.get_april_state().pose.position.x
	rawy = self.get_state().pose.position.y-self.get_april_state().pose.position.y
        if action ==1 and rawx > 0 or action==2 and rawx < 0 or action ==3 and rawy < 0 or action==4 and rawy > 0:
            return 1
        else:
            return -1


    def _killall(self, process_name):
        pids = subprocess.check_output(["pidof",process_name]).split()
        for pid in pids:
            os.system("kill -9 "+str(pid))

    def _reset(self):
        return self.observe()

    def hard_reset(self):
        # Resets the state of the environment and returns an initial observation.
        print "resetting"
        self.reset_model(0)

        self.started = False
        self.new_pose = False
        err = 10
        while not self.new_pose:
            self.rate.sleep()

        while not self.started:
            err = abs(self.pose.pose.position.z - self.cur_pose.pose.position.z)
            if err <.3:
                self.started = True
            self.rate.sleep()
        print "hard world reset"

        return self.observe()
