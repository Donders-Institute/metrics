#!/bin/env python

import os
import sys
import logging

from argparse import ArgumentParser, ArgumentTypeError

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.dirname(os.path.abspath(__file__))+'/external/lib/python')

from utils.Common import *
from utils.Metrics import *

if __name__ == "__main__":
    
    def checkconfig(path):
        if not os.path.exists(path):
            raise ArgumentTypeError('config file not found: %s' % path)
        return path

    parg = ArgumentParser(description='script for pushing cluster (current usage) statistics', version="0.1")

    parg.add_argument('-l','--loglevel',
                      action  = 'store',
                      dest    = 'verbose',
                      choices = [-1, 0, 1, 2],  ## choices work only with str
                      default = 0,
                      type    = int,
                      help    = 'set one of the following verbosity levels. -1:ERROR, 0|default:WARNING, 1:INFO, 2:DEBUG')

    parg.add_argument('-c', '--config',
                      action  = 'store',
                      dest    = 'fconfig',
                      default = os.path.dirname(os.path.abspath(__file__)) + '/etc/config.ini',
                      type    = checkconfig,
                      help    = 'set the configuration parameters, see "config.ini"')

    parg.add_argument('-s', '--sendmail',
                      action  = 'store_true',
                      dest    = 'sendmail',
                      default = False,
                      help    = 'enable sending notification emails at certain circumstances')
                      
    args = parg.parse_args()

    logger = getMyLogger(os.path.basename(__file__))

    vlv = int(args.verbose)
    lv  = logging.ERROR
    if vlv < 0:
        lv = logging.ERROR
    elif vlv == 1:
        lv = logging.INFO
    elif vlv >= 2:
        lv = logging.DEBUG

    logger.setLevel(lv)

    c = getConfig(args.fconfig)

    m = ClusterStatistics(config=args.fconfig, lv=lv)
    m.collectMetrics()

    # email notification (this should be done before pushing the new metrics
    if args.sendmail:
        labels = {}
        mprev = m.getMetrics(name='hpc_stat_node_status', **labels)

        # get list of nodes from previous data that are:
        # - not in 'down' state
        # - in the list of the currently down nodes
        nlist = filter( lambda s:int(s[2]) != -1 and s[1]['host'] in m.nodes_down, mprev )

        logger.debug('notification for nodes are just down: %d nodes' % len(nlist))

        if len(nlist) > 0:
            subject = '[Torquemon] compute nodes DOWN!!'
            msg = '\n'.join(map(lambda x:x[1]['host'], nlist))
            sendEmailNotification('admin@dccn-l018.dccn.nl', c.get('TorqueTracker','NOTIFICATION_EMAILS').split(','), subject, msg)

    # push metrics 
    m.pushMetrics()

    m = MatlabLicenseAccounting(config=args.fconfig, lv=lv)
    m.collectMetrics()
    m.pushMetrics()
