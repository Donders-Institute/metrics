#!/usr/bin/env python
import sys
import os
import logging
import ConfigParser

sys.path.append(os.path.dirname(os.path.abspath(__file__))+'/external/lib/python')

## loading MySQL library for updating the project database
try:
    ## using pure python MySQL client
    import MySQLdb as mdb
except Exception, e:
    ## trying mysql.connector that requires MySQL client library 
    import mysql.connector as mdb
    from mysql.connector import errorcode

def getMySQLConnector(host,uid,passwd,db):
    ''' establishes MySQL connector
    '''
    cnx    = None
    config = None 

    if mdb.__name__ == 'MySQLdb':
        ### use MySQLdb library
        config = {'user'   : uid,
                  'passwd' : passwd,
                  'db'     : db,
                  'host'   : host }
        try:
            cnx = mdb.connect(**config)
        except mdb.Error, e:
            logging.error('db query error %d: %s' % (e.args[0],e.args[1]))

            if cnx: cnx.close()
    else:
        ### use mysql-connector library
        config = {'user'             : uid,
                  'password'         : passwd,
                  'database'         : db,
                  'host'             : host,
                  'raise_on_warnings': True }
        try:
            cnx = mdb.connect(**config)
        except mdb.Error, err:
            if err.errno == errorcode.ER_ACCESS_DENIED_ERROR:
                logging.error("something is wrong with your user name or password")
            elif err.errno == errorcode.ER_BAD_DB_ERROR:
                logging.error("database does not exists")
            else:
                logging.error(err)

            if cnx: cnx.close()

    return cnx

def getConfig(config_file='config.ini'):
    ''' read and parse the config.ini file
    '''
    default_cfg = {
        # Project database interface
        'PDB_USER'         : '',
        'PDB_PASSWORD'     : '',
        'PDB_HOST'         : '',
        'PDB_DATABASE'     : ''
    }

    config = ConfigParser.SafeConfigParser(default_cfg)
    config.read(config_file)

    return config
