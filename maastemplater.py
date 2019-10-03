#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Simple script to prepare machines /w iDRAC for MAAS
"""

import random
import string
import re
from collections import OrderedDict
import logging as log
import argparse

import yaml
from paramiko.client import SSHClient
import paramiko

log.basicConfig(format='%(name)s [%(levelname)s] - %(message)s', level=log.DEBUG)

MAAS_TEMPLATE = """
{hostprefix}{hostno:3d}:
  disk_layout: ${{_param:maas_simple_disk_layout}}
  pxe_interface_mac: {macaddress}
  interfaces:
    nic01:
      type: eth
      name: eno2
      mac: {macaddress}
      subnet: ${{_param:deploy_network_netmask}}
      gateway: ${{_param:deploy_network_gateway}}
      ip: ${{_param:openstack_{hosttype}_node{hostno:2d}_deploy_address}}
      mode: static
  power_parameters:
    power_address: ${{_param:openstack_{hosttype}_node{hostno:2d}_ipmi_address}}
    power_pass: {password_generated}
    power_type: ipmi
    power_user: maas
"""


def ordered_load(stream, Loader=yaml.Loader, object_pairs_hook=OrderedDict):
    """
    Ordered load of params from yaml
    required when setting user options in iDRAC
    """

    class OrderedLoader(Loader):
        pass

    def construct_mapping(loader, node):
        loader.flatten_mapping(node)
        return object_pairs_hook(loader.construct_pairs(node))
    OrderedLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
        construct_mapping)
    return yaml.load(stream, OrderedLoader)


yaml.ordered_load = ordered_load


def random_string_digits(string_length=8):
    """
    Generate a random string of letters and digits
    """
    letters_digits = string.ascii_letters + string.digits
    return ''.join(random.choice(letters_digits) for i in range(string_length))


def load_settings(_file='settings.yaml'):
    """
    Loads settings from yaml file (default: settings.yaml)
    """
    return yaml.ordered_load(open(_file))


def racadm_set(client, racadm_settings):
    """
    Applying racadm settings according to the dictionary
    """
    for group, _object in racadm_settings.items():
        for name, val in _object.items():
            if isinstance(val, dict):
                for index, v in val.items():
                    if name == 'cfgUserAdminPassword':
                        password_generated = random_string_digits()
                        v = password_generated
                        log.debug('password generated: %s', v)
                    if isinstance(v, (str, int)):
                        cmd = "racadm config -g {} -o {} -i {} {}".format(group, name, index, v)
                        _, stdout, stderr = client.exec_command(cmd)
                        stdout, stderr = stdout.read(), stderr.read()
                        if 'successfully' in stdout:
                            log.debug("Successfully set: %s", cmd)
                        else:
                            log.error('stdout:%s stderr:%s', stdout, stderr)
                    else:
                        log.warning(
                            "Unrecognized setting %s in %s:%s:%s", v, group, name, index)
            elif isinstance(val, (str, int)):
                cmd = "racadm config -g {} -o {} {}".format(group, name, val)
                _, stdout, stderr = client.exec_command(cmd)
                stdout, stderr = stdout.read(), stderr.read()
                if 'successfully' in stdout:
                    log.debug("Successfully set: %s", cmd)
                else:
                    log.error('stdout:%s stderr:%s', stdout, stderr)
            else:
                log.warning(
                    "Unrecognized setting %s in %s:%s", val, group, name)
    return password_generated


def racadm_get_mac(client, interface='NIC.Integrated.1-2-1'):
    """
    Fetches MAC Address of boot interface
    """
    log.info("Getting MAC Address for %s", interface)
    log.debug("Running: racadm hwinventory %s", interface)
    _, stdout, _ = client.exec_command('racadm hwinventory {}'.format(interface))
    try:
        mac = re.search(
            r'^Current .*MAC Address:\s+([0-9A-F\:]{17})\s+',
            stdout.read(),
            re.MULTILINE).groups()[0]
    except (AttributeError, IndexError):
        log.warn("Unabled to find MAC Address for %s", interface)
        return None
    return mac.lower()


def render_template(hostprefix, hosttype, hostno, macaddress, password_generated):
    """
    Returns host configuration based on template
    """
    return MAAS_TEMPLATE.format(
        hostprefix=hostprefix,
        hosttype=hosttype,
        hostno=hostno,
        macaddress=macaddress,
        password_generated=password_generated)


if __name__ == '__main__':

    parser = argparse.ArgumentParser(
        description='Reconfigures iDRAC and prepares hosts for MAAS')
    parser.add_argument(
        'maasmachines',
        help='output maas_machines.yml scrape')
    parser.add_argument(
        '--settings_file', '-f',
        default='settings.yaml',
        help='YAML file with settings (look settings.yaml.sample)')
    args = parser.parse_args()

    settings = load_settings(args.settings_file)

    output = open(args.maasmachines, 'w')

    for hostprefix, hosts in settings['hosts'].items():
        for idx, host in enumerate(hosts):
            sshclient = SSHClient()
            sshclient.set_missing_host_key_policy(paramiko.client.AutoAddPolicy)
            log.info("Connecting to host %s", host)
            sshclient.connect(
                host,
                username=settings['credentials']['username'],
                password=settings['credentials']['password'])
            password_gen = racadm_set(sshclient, settings['racadm'])
            log.info('Writing template to %s', args.maasmachines)
            output.write(
                render_template(
                    hostprefix,
                    settings['hosttype'][hostprefix],
                    settings['hosts_start']+idx,
                    racadm_get_mac(sshclient),
                    password_gen))
