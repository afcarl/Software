#!/usr/bin/env python

import os
import rospy
import cv2
from cv_bridge import CvBridge
from fleet_planning.graph_search import GraphSearchProblem
from sensor_msgs.msg import Image
from std_msgs.msg import String
from fleet_planning.srv import *
from fleet_planning.generate_duckietown_map import graph_creator, MapImageCreator
from fleet_planning.transformation import PixelAndMapTransformer
from fleet_planning.duckiebot import *
import json

class MapDrawNode:
    """
    Used to generate the map from a csv file, draw the graph on top
    of that and draw the icons for each duckiebot.
    TODO(ben): add a counter of number of icons at each node and make sure
          to draw overlapping icons next to each other.
    """

    def __init__(self, ):
        print 'mapDraw initializing...'

        self._map_dir = rospy.get_param('/map_dir')
        self._map_name = rospy.get_param('/map_name')
        gui_img_dir = rospy.get_param('/gui_img_dir')

        # Loading paths
        tiles_dir = os.path.abspath(
            self._map_dir + '/../../../../30-localization-and-planning/duckietown_description/urdf/meshes/tiles/')
        customer_icon_path = os.path.join(gui_img_dir, 'customer_duckie.jpg')
        start_icon_path = os.path.join(gui_img_dir, 'duckie.jpg')
        target_icon_path = os.path.join(gui_img_dir, 'location-icon.png')

        # build and init graphs
        gc = graph_creator()
        self.duckietown_graph = gc.build_graph_from_csv(self._map_dir, self._map_name)  # gc.build_graph_from_csv(script_dir=self.script_dir, csv_filename=self.map_name)
        self.duckietown_problem = GraphSearchProblem(self.duckietown_graph, None, None)  # GraphSearchProblem(self.duckietown_graph, None, None)

        self.bridge = CvBridge()
        # prepare and send graph image through publisher
        self.graph_image = self.duckietown_graph.draw(map_dir=self._map_dir, highlight_edges=None, map_name=self._map_name)

        mc = MapImageCreator(tiles_dir)
        self.tile_length = mc.tile_length
        self.map_img = mc.build_map_from_csv(map_dir=self._map_dir, csv_filename=self._map_name)

        # keep track of how many icons are being drawn at each node
        self.num_duckiebots_per_node = {node: 0 for node in self.duckietown_graph._nodes}

        # init items for later drawing
        self._duckie_path_to_draw = ""
        self.pending_customer_requests = []
        self.duckiebots = {}

        # image used to store all start, customer and target icons at their positions
        self.customer_icon = cv2.resize(cv2.imread(customer_icon_path), (30, 30))
        self.start_icon = cv2.resize(cv2.imread(start_icon_path), (30, 30))
        self.target_icon = cv2.resize(cv2.imread(target_icon_path), (30, 30))

        print "Map loaded successfully!\n"

        # publishers
        self._pub_image = rospy.Publisher("~/map_graph", Image, queue_size=1, latch=True)

        # subscribers
        self._sub_draw_request = rospy.Subscriber('~/draw_request', String,
                                                       self._draw_and_publish_image, queue_size=1)
        self._sub_draw_duckie_ = rospy.Subscriber('~/draw_path',String, self._draw_path, queue_size=1)

        self._draw_and_publish_image(None) # publish map without duckies

    def graph_node_to_image_location(self, graph, node):
        """
        Convert a graph node number to a 2d image pixel location
        """
        return graph.node_positions[str(node)]

    def draw_icons(self, map_image, icon_type, location, icon_number=1):
        """
        Draw start, customer and target icons next to each
        corresponding graph node along with the respective name
        of the duckiebot.
        :param map_image: the base map image onto which to draw the icons
        :param icon_type: string, either customer, start or target
        :param location: where to draw the icon, as a graph node number
        :param icon_number: keeps track of how many icons have already
                            been drawn at this location; adjust position
                            accordingly

        :return opencv image with the icons at the correct positions
        """
        # loop through all trips currently in existence. For each trip,
        # draw the start, customer and target icons next to the corresponding
        # label of the graph node.
        transf = PixelAndMapTransformer(self.tile_length, self.map_img.shape[
            0] / self.tile_length)  # TODO: better way to get the map dimensions?
        if icon_type == "customer":
            icon = self.customer_icon
        elif icon_type == "start":
            icon = self.start_icon
            # TODO: add the name of the duckiebot to the icon. Maybe overlay icons if e.g. duckiebot is at targets
        elif icon_type == "target":
            icon = self.target_icon
            print "drawing target"
        else:
            rospy.logwarn("{} is an invalid icon type.".format(icon_type))
            # return

        # convert graph number to 2D image pixel coords
        point = self.graph_node_to_image_location(graph=self.duckietown_graph, node=location)
        point = transf.map_to_image(point)
        # check if point is in map - NOTE that the coordinates of point are 
        # image coordinates (height by width, i.e. y by x and not x by y),
        # starting at the top left, thus having a negative sign.
        if (point[1] * -1 > map_image.shape[0] or point[1] * -1 < 0 or point[0] > map_image.shape[1] or point[0] < 0):
            rospy.logwarn("Point ({},{}) is outside of the map!".format(point[1], point[0]))

        # NOTE: factor -1 added so due to image negative image coordinates in vertical direction.
        height_start = max(self.map_img.shape[0] * -1, point[1] * -1)  
        height_end = min(self.map_img.shape[0], (height_start + icon.shape[0]))
        print "height end as max: ", self.map_img.shape[0] * -1, (height_start + icon.shape[0])
        width_start =  min(self.map_img.shape[1], point[0] + (icon_number - 1) * (icon.shape[1] + 5))
        width_end = min(self.map_img.shape[1], width_start + icon.shape[1])
        icon = icon[0:height_end - height_start, 0:width_end - width_start]
        print "point: ", point
        print "map: ", self.map_img.shape
        print "coords: ", height_start, height_end, width_start, width_end
        print "icon shape: ", icon.shape, "icon type: ", icon_type
        map_image[height_start:height_end, width_start:width_end, :] = icon

        return map_image

    def prepImage(self):
        """takes the graph image and map image and overlays them"""
        # TODO: add the icon image and merge it as well
        inverted_graph_img = 255 - self.graph_image
        # bring to same size
        inverted_graph_img = cv2.resize(inverted_graph_img, (self.map_img.shape[1], self.map_img.shape[0]))

        # overlay images
        overlay = cv2.addWeighted(inverted_graph_img, 1, self.map_img, 0.5, 0)

        # make the image bright enough for display again
        hsv = cv2.cvtColor(overlay, cv2.COLOR_BGR2HSV)
        h, s, v = cv2.split(hsv)
        lim = 255 - 60
        v[v > lim] = 255
        v[v <= lim] += 60
        final_hsv = cv2.merge((h, s, v))

        overlay = cv2.cvtColor(final_hsv, cv2.COLOR_HSV2BGR)
        return overlay

    def drawMap(self, duckiebots, pending_customer_requests=[], duckie_path_to_draw = ""):
        """
        New function to draw map independent of GUI calls. Draw all duckiebots
        and their customers, if they have any.
        Input:
            - duckiebots: all duckiebots that should be drawn
        """
        # TODO: figure out best way to visualize duckiebot with customer
        #todo get updated graph (with path)
        edges_to_draw = None
        if (self._duckie_path_to_draw):
            node_numbers_as_str = self.duckiebots[self._duckie_path_to_draw].path
            edges_to_draw = []
            for i in range(0,len(node_numbers_as_str)-1):
                node = node_numbers_as_str[i]
                next_node = node_numbers_as_str[i+1]
                edges = self.duckietown_graph.node_edges(node)
                for edge in edges:
                    if edge.source == node and edge.target == next_node:
                        edges_to_draw.append(edge)
                        break

        self.graph_image = self.duckietown_graph.draw(map_dir=self._map_dir, highlight_edges=edges_to_draw, map_name=self._map_name)

        overlay = self.prepImage()
        for bot in duckiebots.itervalues():
            # draw duckiebot
            self.num_duckiebots_per_node[str(bot.location)] += 1
            overlay = self.draw_icons(overlay, "start", location=bot.location,
                                      icon_number=self.num_duckiebots_per_node[str(bot.location)])

            if bot.taxi_state != TaxiState.IDLE:
                if bot.taxi_state == TaxiState.GOING_TO_CUSTOMER:
                    customer_location = bot.target_location

                if bot.taxi_state == TaxiState.WITH_CUSTOMER:
                    customer_location = bot.location

                # draw customer
                self.num_duckiebots_per_node[str(customer_location)] += 1
                overlay = self.draw_icons(overlay, "customer",
                                          location=customer_location, icon_number=self.num_duckiebots_per_node[str(customer_location)])  # TODO(ben): figure out an unambiguous set of icons and assign the correct ones

                # draw target icon
                self.num_duckiebots_per_node[str(bot.target_location)] += 1
                overlay = self.draw_icons(overlay, "target", location=bot.target_location, icon_number=self.num_duckiebots_per_node[str(bot.target_location)])

        # draw pending customer requests
        for customer in pending_customer_requests:
            self.num_duckiebots_per_node[str(customer.start_location)] += 1
            overlay = self.draw_icons(overlay, "customer",
                                      location=customer.start_location, icon_number=self.num_duckiebots_per_node[str(customer.start_location)])

        # set num_duckiebots_per_node back to zero
        for node, num in self.num_duckiebots_per_node.iteritems():
            self.num_duckiebots_per_node[node] = 0

        return self.bridge.cv2_to_imgmsg(overlay, "bgr8")

    def draw_duckiebot_path(self):
        # TODO: draw path for duckiebot. Only draw path for the latest request? Or the one chosen in the GUI?
        # Checkout the highlight edges thing in graph.py. Maybe need to add a function there?
        pass

    def _draw_and_publish_image(self, msg):

        if msg is None:
            map_img = self.drawMap({}, [], '')
        else:
            data_json = msg.data
            data = json.loads(data_json)

            # get duckiebot objects
            self.duckiebots = {}
            for db_data in data['duckiebots']:
                bot = BaseDuckiebot.from_json(db_data)
                self.duckiebots[bot.name]=bot

            # get customer request objects
            self.pending_customer_requests = [
                BaseCustomerRequest.from_json(cust) for cust in data['pending_customer_requests'] if cust is not None]

            map_img = self.drawMap(self.duckiebots, self.pending_customer_requests, self._duckie_path_to_draw)

        rospy.loginfo('Publish new map.')
        self._pub_image.publish(map_img)


    def _draw_path(self,duckie_name):
        if duckie_name is not None:
            self._duckie_path_to_draw = duckie_name
            map_img = self.drawMap(self.duckiebots, self.pending_customer_requests, self._duckie_path_to_draw)
            rospy.loginfo('Publish new map.')
            self._pub_image.publish(map_img)

    @staticmethod
    def on_shutdown():
        rospy.loginfo("[MapDraw] Shutdown.")


if __name__ == '__main__':
    # startup node
    rospy.loginfo('[MapDrawNode]: Startup.')
    rospy.init_node('MapDrawNode')
    map_draw_node = MapDrawNode()

    rospy.on_shutdown(MapDrawNode.on_shutdown)
    rospy.spin()
