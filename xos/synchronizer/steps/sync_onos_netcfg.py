
# Copyright 2017-present Open Networking Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


import os
import requests
import socket
import sys
import base64
import json
from synchronizers.new_base.syncstep import SyncStep
from synchronizers.new_base.modelaccessor import *
from xos.logger import Logger, logging

logger = Logger(level=logging.INFO)

class SyncONOSNetcfg(SyncStep):
    provides=[VTNService]
    observes=VTNService
    watches=[ModelLink(Node,via='node'), ModelLink(AddressPool,via='addresspool')]
    requested_interval=0

    def __init__(self, **args):
        SyncStep.__init__(self, **args)

    def handle_watched_object(self, o):
        logger.info("handle_watched_object is invoked for object %s" % (str(o)),extra=o.tologdict())
        if (type(o) is Node): # For Node add/delete/modify
            self.call()
        if (type(o) is AddressPool): # For public gateways
            self.call()

    def get_node_tag(self, node, tagname):
        tags = Tag.objects.filter(content_type=model_accessor.get_content_type_id(node),
                                  object_id=node.id,
                                  name=tagname)
        return tags[0].value

    def get_service_instances_who_want_config(self):
        service_instances = []
        # attribute is comma-separated list
        for ta in ServiceInstanceAttribute.objects.filter(name="autogenerate"):
            if ta.value:
                for config in ta.value.split(','):
                    if config == "vtn-network-cfg":
                        service_instances.append(ta.service_instance)
        return service_instances

    def save_service_instance_attribute(self, service_instance, name, value):
        # returns True if something changed
        something_changed=False
        tas = ServiceInstanceAttribute.objects.filter(service_instance_id=service_instance.id, name=name)
        if tas:
            ta = tas[0]
            if ta.value != value:
                logger.info("updating %s with attribute" % name)
                ta.value = value
                ta.save(update_fields=["value", "updated"], always_update_timestamp=True)
                something_changed = True
        else:
            logger.info("saving autogenerated config %s" % name)
            ta = model_accessor.create_obj(ServiceInstanceAttribute, service_instance=service_instance, name=name, value=value)
            ta.save()
            something_changed = True
        return something_changed

    # This function currently assumes a single Deployment and Site
    def get_onos_netcfg(self, vtn):
        privateGatewayMac = vtn.privateGatewayMac
        localManagementIp = vtn.localManagementIp
        ovsdbPort = vtn.ovsdbPort
        sshPort = vtn.sshPort
        sshUser = vtn.sshUser
        sshKeyFile = vtn.sshKeyFile
        mgmtSubnetBits = vtn.mgmtSubnetBits
        xosEndpoint = vtn.xosEndpoint
        xosUser = vtn.xosUser
        xosPassword = vtn.xosPassword

        controllerPort = vtn.controllerPort
        if ":" in controllerPort:
            (c_hostname, c_port) = controllerPort.split(":",1)
            controllerPort = socket.gethostbyname(c_hostname) + ":" + c_port
        else:
            controllerPort = ":" + controllerPort

        data = {
            "apps" : {
                "org.opencord.vtn" : {
                    "cordvtn" : {
                        "privateGatewayMac" : privateGatewayMac,
                        "localManagementIp": localManagementIp,
                        "ovsdbPort": ovsdbPort,
                        "ssh": {
                            "sshPort": sshPort,
                            "sshUser": sshUser,
                            "sshKeyFile": sshKeyFile
                        },
                        "xos": {
                            "endpoint": xosEndpoint,
                            "user": xosUser,
                            "password": xosPassword
                        },
                        "publicGateways": [],
                        "nodes" : [],
                        "controllers": [controllerPort]
                    }
                }
            }
        }

        # Generate apps->org.opencord.vtn->cordvtn->openstack
        controllers = Controller.objects.all()
        if controllers:
            controller = controllers[0]
            keystone_server = controller.auth_url
            user_name = controller.admin_user
            tenant_name = controller.admin_tenant
            password = controller.admin_password
            openstack = {
                "endpoint": keystone_server,
                "tenant": tenant_name,
                "user": user_name,
                "password": password
            }
            data["apps"]["org.opencord.vtn"]["cordvtn"]["openstack"] = openstack

        # Generate apps->org.opencord.vtn->cordvtn->nodes
        nodes = Node.objects.all()
        for node in nodes:
            try:
                nodeip = socket.gethostbyname(node.name)
            except socket.gaierror:
                logger.warn("unable to resolve hostname %s: node will not be added to config"
                            % node.name)
                continue

            try:
                bridgeId = self.get_node_tag(node, "bridgeId")
                dataPlaneIntf = self.get_node_tag(node, "dataPlaneIntf")
                dataPlaneIp = self.get_node_tag(node, "dataPlaneIp")
            except:
                logger.error("not adding node %s to the VTN configuration" % node.name)
                continue

            node_dict = {
                "hostname": node.name,
                "hostManagementIp": "%s/%s" % (nodeip, mgmtSubnetBits),
                "bridgeId": bridgeId,
                "dataPlaneIntf": dataPlaneIntf,
                "dataPlaneIp": dataPlaneIp
            }

            # this one is optional
            try:
                node_dict["hostManagementIface"] = self.get_node_tag(node, "hostManagementIface")
            except IndexError:
                pass

            data["apps"]["org.opencord.vtn"]["cordvtn"]["nodes"].append(node_dict)

        # Generate apps->org.onosproject.cordvtn->cordvtn->publicGateways
        # Pull the gateway information from Address Pool objects
        for ap in AddressPool.objects.all():
            if (not ap.gateway_ip) or (not ap.gateway_mac):
                logger.info("Gateway_ip or gateway_mac is blank for addresspool %s. Skipping." % ap)
                continue

            gateway_dict = {
                "gatewayIp": ap.gateway_ip,
                "gatewayMac": ap.gateway_mac
            }
            data["apps"]["org.opencord.vtn"]["cordvtn"]["publicGateways"].append(gateway_dict)

        if not AddressPool.objects.all().exists():
            logger.info("No Address Pools present, not adding publicGateways to config")

        return json.dumps(data, indent=4, sort_keys=True)

    # TODO: Does this step execute every 5 seconds regardless of whether objects have changed?
    #       If so, what purpose does using watchers serve?

    def call(self, **args):
        vtn_service = VTNService.objects.all()
        if not vtn_service:
            raise Exception("No VTN Service")

        vtn_service = vtn_service[0]

        # Check for autogenerate attribute
        netcfg = self.get_onos_netcfg(vtn_service)

        service_instances = self.get_service_instances_who_want_config()
        for service_instance in service_instances:
            something_changed = self.save_service_instance_attribute(service_instance, "rest_onos/v1/network/configuration/", netcfg)
            if (something_changed):
                # make sure we cause the ServiceInstance to be updated as well as the attribute
                # TODO: revisit this after synchronizer multi-object-watching is complete
                service_instance.save(update_fields=["updated"], always_update_timestamp=True)
