#!/usr/bin/env python
import json
import random
import pycurl
import sys
import os
import re
import logging
from datetime import date
from datetime import datetime
from datetime import timedelta
from argparse import ArgumentParser
from HTMLParser import HTMLParser

# load utility libraries
from utils.Common import getConfig, getMySQLConnector

class Callback:
    def __init__(self):
        self.header   = ''
        self.contents = ''

    def header_callback(self, buf):
        self.header = self.header + buf

    def body_callback(self, buf):
        self.contents = self.contents + buf

    def progress_callback(self, download_t, download_d, upload_t, upload_d):
        logging.info('uploaded %d:%d', upload_d, upload_t)

# create a subclass and override the handler methods
class LabUsageHTMLParser(HTMLParser):

    def __init__(self):
        HTMLParser.__init__(self)
        self.headers = []
        self.items = []

        self.isHeader = False
        self.isData = False

        self.icol = -1 

    def handle_starttag(self, tag, attrs):
        self.isHeader = ( tag.lower() == 'th' )

        # row start
        if tag.lower() == 'tr':
            self.items.append({})
            return

        # data colume start
        if tag.lower() == 'td':
            self.icol += 1
            return

        # header colume start
        if self.isHeader:
            return
 
    def handle_endtag(self, tag):

        if self.isHeader:
            return

        if tag.lower() == 'tr':
            self.icol = -1
            return

    def handle_data(self, data):
        if self.isHeader:
            self.headers.append(data)
            return
        elif self.icol >= 0:
            k = self.headers[self.icol]
            self.items[-1][k] = data
            return

class Label:

    def __init__(self, **kwargs):
        self.source = ''
        self.project = ''
        self.bill = ''
        self.lab = ''
        self.group = ''
        self.status = ''
        self.__dict__.update(kwargs)

    def __str__(self):
        return repl(self.__dict__) 

    def __repr__(self):
        return repr(self.__dict__)

    def __eq__(self, other):
        if isinstance(other, self.__class__):
            return self.__dict__ == other.__dict__
        else:
            return False

    def __ne__(self, other):
        return not self.__eq__(other)

    def __cmp__(self, other):
        return self.__eq__(other)

def getLabUsageReport(beg=None, end=None):
    '''download the report HTML table and parse it into booking events'''

    data = []

    if not beg or not end:
        return data

    t = Callback()

    # TODO: make URL endpoint configurable
    url="http://intranet.donders.ru.nl/apps/projects/projects/report_labusage/%s/%s" % (beg, end)

    t = Callback()
    c = pycurl.Curl()
    c.setopt(c.URL, url)
    c.setopt(c.CONNECTTIMEOUT, 3)
    c.setopt(c.TIMEOUT, 10)
    c.setopt(c.HEADERFUNCTION, t.header_callback)
    c.setopt(c.WRITEFUNCTION , t.body_callback)
    c.setopt(c.FOLLOWLOCATION , long(1))
    c.setopt(c.SSL_VERIFYPEER, long(0))
    c.setopt(c.CUSTOMREQUEST , 'GET')
    c.perform()
    code = c.getinfo(pycurl.HTTP_CODE)
    c.close()

    if code >= 400:
        logging.error('%s',t.header)
        logging.error('%s',t.contents)

    htmlDoc = ''.join(t.contents.split('\n'))

    p = LabUsageHTMLParser()

    p.feed(htmlDoc)

    return p.items

def pushMetric(m):
    '''push metrics to OpenTSDB'''

    # TODO: make URL endpoint configurable
    url="http://stager.dccn.nl:9242/api/put"

    t = Callback()
    c = pycurl.Curl()
    c.setopt(c.URL, url)
    c.setopt(c.CONNECTTIMEOUT, 3)
    c.setopt(c.TIMEOUT, 10)
    c.setopt(c.HEADERFUNCTION, t.header_callback)
    c.setopt(c.WRITEFUNCTION , t.body_callback)
    c.setopt(c.CUSTOMREQUEST , 'POST')
    c.setopt(c.POSTFIELDSIZE , len(m))
    c.setopt(c.POSTFIELDS    , m)
    c.perform()
    code = c.getinfo(pycurl.HTTP_CODE)
    c.close()

    if code >= 400:
        logging.error('%s', t.header)
        logging.error('%s', t.contents)

def labelize(s):
    '''replace disallowed label characters for OpenTSDB'''

    return s.replace('  ', ' ').replace(' ','_').replace('(','/').replace(')','/').replace(',','.').replace('&', 'and')

def resolveLabUsageGaps(bookings, date, lab, tbegDay='08:00:00', tendDay='17:00:00'):
    '''resolve unused lab time from the booking events'''

    items = {}

    if not bookings and ( not date or lab):
        raise ValueError('either bookings or date and lab should be provided')

    if not bookings:

        if not date:
            date = date.today().strftime('%Y-%m-%d')

        t1 = datetime.strptime('%s %s' % (date, tbegDay), '%Y-%m-%d %H:%M:%S')
        t2 = datetime.strptime('%s %s' % (date, tendDay), '%Y-%m-%d %H:%M:%S')

        dt = (t2 - t1).total_seconds() / 60.

        if not lab:
            lab = 'all'

        items[date] = [{ 'tags': {'lab': lab}, 'value': int(dt), 'timestamp': long(t1.strftime('%s'))}]
    else:

        # select only events of interests:
        # - Status in ['CANCELLEDNIT', 'TENTATIVE', 'CONFIRMED' ]
        # - Source id DOES NOT START with ['3055', '30100']
        _bookings = filter( lambda x:x['Status'] in ['CANCELLEDNIT','TENTATIVE','CONFIRMED'], bookings )
        _bookings = filter( lambda x:re.match(r'^(?!(3055|30100))', x['Source']), _bookings )

        # resolve the date range from the bookings
        dates = list(set(map( lambda x:datetime.strptime(x['Bookings'].split()[0], '%Y-%m-%d').date(), _bookings )))
        labs = list(set(map( lambda x:x['Calendar'], _bookings )))

        # make new list of booking events in which the 'Bookings' is converted into the begin and end time of the event
        reBookings = re.compile('([0-9]{4}-[0-9]{2}-[0-9]{2})\s\(([0-9]{2}:[0-9]{2})-([0-9]{2}:[0-9]{2})\)')
        events = []
        for e in _bookings:
            m = reBookings.match(e['Bookings'])
            if not m:
                continue
            else:
                d = m.group(1)
                tbeg = datetime.strptime( '%s %s:00' % (m.group(1), m.group(2)), '%Y-%m-%d %H:%M:%S' )
                tend = datetime.strptime( '%s %s:00' % (m.group(1), m.group(3)), '%Y-%m-%d %H:%M:%S' )
                events.append([])
                events[-1] = { 'lab'    : e['Calendar'],
                               'source' : e['Source'],
                               'project': e['Project number'],
                               'status' : e['Status'],
                               'tbeg'   : tbeg,
                               'tend'   : tend } 

        # resolve gaps between booking events, and organise them by date and lab
        items = {}
        for d in dates:
            items[ d.strftime('%Y-%m-%d') ] = [] 

        for d in dates:
            for l in labs:
                # select booking events in the given date and lab
                eot = filter(lambda x:x['lab'] == l and x['tbeg'].date() == d, events)
                eot = sorted( eot, key=lambda x:x['tbeg'] )

                eot.insert(0, {'tend': datetime.strptime('%s %s' % (d, tbegDay), '%Y-%m-%d %H:%M:%S')})
                eot.append({'tbeg': datetime.strptime('%s %s' % (d, tendDay), '%Y-%m-%d %H:%M:%S')})

                #if l == 'Prisma':
                #    for e in eot:
                #        print e
                for i in xrange(len(eot)-1):
                    if eot[i]['tend'] >= eot[i+1]['tbeg']:
                        continue 

                    dt = ( eot[i+1]['tbeg'] - eot[i]['tend'] ).total_seconds() / 60.

                    items[d.strftime('%Y-%m-%d')].append( {'tags': {'lab': labelize(l)}, 'value': int(dt), 'timestamp': long(eot[i]['tend'].strftime('%s'))} )

    return items

def getProjectGroupMap(cfg):

    groups = {}

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
            qry  = 'select p.id , u.group_id, g.description from projects p, users u, groups g '
            qry += 'where p.owner_id = u.id and u.group_id = g.id'

            crs.execute(qry)

            for (project, gid, gname) in crs:
                groups[project] = {'id': gid, 'name': gname}

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

    return groups

def eventFilter(e):

    for k in ['Source', 'Calendar', 'Status', 'Duration (hours)']:
        if k not in e.keys():
            return False

    if float(e['Duration (hours)']) <= 0:
        return False

    return True

if __name__ == "__main__":

    parg = ArgumentParser(description='sets/adds access rights to project storage', version="0.1")

    ## optional arguments
    parg.add_argument('-l','--loglevel',
                      action  = 'store',
                      dest    = 'verbose',
                      choices = ['info', 'debug', 'warning', 'error'],
                      default = 'info',
                      help = 'set the logging level')

    parg.add_argument('-n','--name-metrics-used',
                      action  = 'store',
                      dest    = 'tsname_used',
                      default = 'lab.usage',
                      help = 'specify the name of the time series for the lab usage')

    parg.add_argument('-c','--config',
                      action  = 'store',
                      dest    = 'config',
                      default = os.path.dirname(os.path.abspath(__file__)) + '/etc/config.ini',
                      help = 'specify the configuration file path')    

    parg.add_argument('-g','--name-metrics-unused',
                      action  = 'store',
                      dest    = 'tsname_free',
                      default = 'lab.free',
                      help = 'specify the name of the time series for unused lab slots')

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

    pgmap = getProjectGroupMap(cfg)

    tbeg = datetime.strptime(args.dateFrom, '%Y-%m-%d')
    tend = datetime.strptime(args.dateTo  , '%Y-%m-%d')
    dt = timedelta(days=1)

    while tbeg <= tend:
        t = tbeg.strftime('%Y-%m-%d')
        ts = tbeg + timedelta(hours=12)
        tbeg += dt

        logging.info('creating metrics for %s' % t)

        # filter out the items without 'Source' field in the retrieved lab usage report 
        items = filter( lambda x:eventFilter(x), getLabUsageReport(beg=t, end=t) )

        values = []
        labels = []
 
        for d in items:
            duration = float(d['Duration (hours)'])
            try:
                l = Label(source=d['Source'],
                          project=d['Project number'],
                          group=labelize(pgmap[d['Project number']]['name']),
                          status=d['Status'],
                          lab=labelize(d['Calendar']),
                          bill=labelize(d['Billing']))
                try: 
                    id = labels.index(l)
                    values[id] += duration
                except ValueError:
                    labels.append(l)
                    values.append(duration)
            except:
                logging.warning('tag(s) not found, item skipped: %s', repr(d))

        for id in xrange(len(labels)):
            m = {}
            m['metric'] = args.tsname_used
            m['value'] = values[id]
            m['timestamp'] = long(ts.strftime('%s'))
            m['tags'] = labels[id].__dict__

            if not args.dryrun:
                logging.debug('%s: %s', t, json.dumps(m))
                pushMetric( json.dumps(m) )
            else:
                logging.info('%s: %s', t, json.dumps(m))

        logging.debug('%d items --> %d data points', len(items), len(labels))

        # resolve gaps of the day
        gaps = resolveLabUsageGaps(bookings=items, date=t, lab='', tbegDay='08:30:00', tendDay='18:00:00')

        for d in sorted(gaps.keys()):
            for g in gaps[d]:
                g['metric'] = args.tsname_free
                if not args.dryrun:
                    logging.debug('%s: %s', d, json.dumps(g))
                    pushMetric( json.dumps(g) )
                else:
                    logging.info('%s: %s', d, json.dumps(g))
