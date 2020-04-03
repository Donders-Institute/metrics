#!/usr/bin/env python
import json
import random
import pycurl
import sys
import os 
import logging
from datetime import datetime
from datetime import timedelta
from argparse import ArgumentParser
from HTMLParser import HTMLParser

# load utility libraries
from utils.Common import getConfig, getMySQLConnector, pushMetric
from utils.Common import CURLCallback as Callback

def getFilerUsageReport(cfg, fromDate, toDate):

    data = {}

    if not fromDate or not toDate:
        logging.error('missing date rage')
        return data    

    db_host = cfg.get('PDB','PDB_HOST')
    db_uid  = cfg.get('PDB','PDB_USER')
    db_pass = cfg.get('PDB','PDB_PASSWORD')
    db_name = cfg.get('PDB','PDB_DATABASE')

    cnx = getMySQLConnector(db_host, db_uid, db_pass, db_name)

    if not cnx:
        logging.error('Project DB connection failed')
    else:
        crs = None
        try:
            ## get the db cursor
            crs = cnx.cursor()
        
            ## select actions that are not activted 
            qry  = 'select UNIX_TIMESTAMP(created),'
            qry += 'aggregate,'
            qry += 'aggregate_usedsize/1024 as used,'
            qry += 'aggregate_availsize/1024 as avail,'
            qry += 'aggregate_size/1024 as total '
            qry += 'from fileserver_stats where DATE(created) between %s and %s'
            #qry += 'group by created'

            crs.execute(qry, [fromDate, toDate])
           
            ## a construction to avoid double counting multiple data points
            ## that are reported for the same aggregate during the day.
            aggrs = []
            sum_used  = 0
            sum_avail = 0
            sum_total = 0
            for (ts, aggr, used, avail, total) in crs:

                if aggr in aggrs:
                    continue

                aggrs.append(aggr)
                sum_used  += used
                sum_avail += avail
                sum_total += total

            date = datetime.fromtimestamp(ts).strftime('%Y-%m-%d')
            data[date] = { 'used': long(sum_used), 'avail': long(sum_avail), 'total': long(sum_total) }

        except Exception, e:
            logging.exception('Project DB select failed')
        else:
            ## everything is fine
            logging.info('Project DB select succeeded')
        finally:
            ## close db cursor
            try:
                crs.close()
            except Exception, e:
                pass

            ## close db connection
            try:
                cnx.close()
            except Exception, e:
                pass

    return data

if __name__ == "__main__":

    parg = ArgumentParser(description='report filer usage to the openTSDB metrics', version="0.1")

    ## optional arguments
    parg.add_argument('-l','--loglevel',
                      action  = 'store',
                      dest    = 'verbose',
                      choices = ['info', 'debug', 'warning', 'error'],
                      default = 'info',
                      help = 'set the logging level')

    parg.add_argument('-n','--name',
                      action  = 'store',
                      dest    = 'tsname',
                      default = 'storage.filer.size',
                      help = 'specify the name of the time series')

    parg.add_argument('-c','--config',
                      action  = 'store',
                      dest    = 'config',
                      default = os.path.dirname(os.path.abspath(__file__)) + '/etc/config.ini',
                      help = 'specify the configuration file path')    

    parg.add_argument('-f','--from',
                      action  = 'store',
                      dest    = 'dateFrom',
                      default = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
                      help = 'set the starting date from which the metrics is created')    

    parg.add_argument('-t','--to',
                      action  = 'store',
                      dest    = 'dateTo',
                      default = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'),
                      help = 'set the ending date to which the metrics is created')

    parg.add_argument('-d','--dry',
                      action  = 'store_true',
                      dest    = 'dryrun',
                      default = False,
                      help = 'perform dry run: print the metrics datapoints without ingesting them into time-series database')

    args = parg.parse_args()

    # load configuration parameters
    cfg = getConfig( args.config )

    logging.basicConfig(format='[%(levelname)-8s] %(message)s',level=logging.__dict__[args.verbose.upper()])

    data = getFilerUsageReport(cfg, args.dateFrom, args.dateTo)

    for d in sorted(data.keys()):
        mlist = []
        for k in data[d].keys():
            mlist.append({})
            mlist[-1]['timestamp'] = long( (datetime.strptime(d, '%Y-%m-%d') + timedelta(hours=12)).strftime('%s') )
            mlist[-1]['metric'] = args.tsname
            mlist[-1]['value']  = data[d][k]
            mlist[-1]['tags']   = { 'type': k }
        if not args.dryrun:
            logging.debug('%s', json.dumps(mlist))
            pushMetric( cfg.get('OpenTSDB','URL_PUSH'), json.dumps(mlist) )
        else:
            logging.info('%s', json.dumps(mlist))
