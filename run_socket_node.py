import sqlite3
from gevent import monkey

monkey.patch_all(thread=False)

import time
import random
import logging
import os
import pickle
import re

from datetime import datetime, timezone
import pytz

import ntplib


from gevent import Greenlet
from myexperiements.sockettest.dumbo_node import DumboBFTNode
from network.socket_server import NetworkServer
from network.socket_client import NetworkClient
from multiprocessing import Value as mpValue, Queue as mpQueue
from ctypes import c_bool
from dumbobft.core.tx_generator import inter_tx_generator

from collections import namedtuple
from enum import Enum
from honeybadgerbft.exceptions import UnknownTagError
import traceback
from gevent.queue import Queue
import gevent

server_bft_mpq = mpQueue()

'''
def fetch_time_bias(ntp_server="pool.ntp.org"):
    num = 0
    while True:
        try:
            ntp_client = ntplib.NTPClient()
            response = ntp_client.request(ntp_server, version=3, timeout=1)
            now = datetime.now(timezone.utc)
            utc_time = response.tx_time
            time_bias = utc_time - now.timestamp()
            return time_bias
        except Exception as e:
            print("Failed to fetch NTP time:", e, num)
            num += 1
            continue
'''

def parse_shard_info(tx):
    input_shards = re.findall(r'Input Shard: (\[.*?\])', tx)[0]
    input_valids = re.findall(r'Input Valid: (\[.*?\])', tx)[0]
    output_shard = re.findall(r'Output Shard: (\d+)', tx)[0]
    output_valid = re.findall(r'Output Valid: (\d+)', tx)[0]

    input_shards = eval(input_shards)
    input_valids = eval(input_valids)
    output_shard = int(output_shard)
    output_valid = int(output_valid)

    return input_shards, input_valids, output_shard, output_valid

def read_pkl_file(file_path):
    with open(file_path, 'rb') as f:
        data = pickle.load(f)
    return data

def set_consensus_log(id: int):
    logger = logging.getLogger("consensus-node-" + str(id))
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        '%(asctime)s %(filename)s [line:%(lineno)d] %(funcName)s %(levelname)s %(message)s ')
    if 'log' not in os.listdir(os.getcwd()):
        os.mkdir(os.getcwd() + '/log')
    full_path = os.path.realpath(os.getcwd()) + '/log/' + "consensus-node-" + str(id) + ".log"
    file_handler = logging.FileHandler(full_path)
    file_handler.setFormatter(formatter)  # 可以通过setFormatter指定输出格式
    logger.addHandler(file_handler)
    return logger

if __name__ == '__main__':

    # ================================================================================
    # Set the meaning of the command line arguments and initialize them
    # ================================================================================

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--sid', metavar='sid', required=True,
                        help='identifier of node', type=str)
    parser.add_argument('--id', metavar='id', required=True,
                        help='identifier of node', type=int)
    parser.add_argument('--shard_id', metavar='shard_id', required=True,
                        help='identifier of shard', type=int)
    parser.add_argument('--tx_num', metavar='tx_num', required=True,
                        help='number of transactions', type=int)
    parser.add_argument('--shard_num', metavar='shard_num', required=True,
                        help='number of shards', type=int)
    parser.add_argument('--N', metavar='N', required=True,
                        help='number of parties', type=int)
    parser.add_argument('--f', metavar='f', required=True,
                        help='number of faulties', type=int)
    parser.add_argument('--B', metavar='B', required=True,
                        help='size of batch', type=int)
    parser.add_argument('--K', metavar='K', required=False,
                        help='rounds to execute', type=int, default=1)
    parser.add_argument('--R', metavar='R', required=True,
                        help='number of rounds', type=int)
    parser.add_argument('--S', metavar='S', required=False,
                        help='slots to execute', type=int, default=50)
    parser.add_argument('--M', metavar='M', required=False,
                        help='whether to mute a third of nodes', type=bool, default=False)
    parser.add_argument('--D', metavar='D', required=False,
                        help='whether to debug mode', type=bool, default=False)
    parser.add_argument('--Crate', metavar='Crate', required=False,
                        help='percentage of cross-shard transcation', type=float, default=0.1)
    args = parser.parse_args()

    sid, i, shard_id, tx_num, shard_num, N, f, B, K, R, S, M, D, Crate = (
        args.sid, args.id, args.shard_id, args.tx_num, args.shard_num, args.N, args.f, args.B, args.K, args.R, args.S, args.M, args.D, args.Crate)
    rnd = random.Random(sid)


    # ================================================================================
    # Initialize sockets configration of nodes
    # ================================================================================

    addresses = [None] * shard_num * N
    with open('hosts.config', 'r') as hosts:
        for line in hosts:
            params = line.split()
            pid, priv_ip, pub_ip, port = (
                int(params[0]), params[1], params[2], int(params[3]))
            if pid not in range(shard_num * N):
                continue
            if pid == i + shard_id * N:
                my_address = (priv_ip, port)
            addresses[pid] = (pub_ip, port)
    assert all([node is not None for node in addresses])
    #print("hosts.config is correctly read")

    # ================================================================================
    # Initialize network of the node
    # ================================================================================

    client_bft_mpq = mpQueue()
    server_bft_mpq = mpQueue()

    #lambda functions used to communicate
    client_from_bft = lambda: client_bft_mpq.get(timeout=0.1)
    bft_to_client = client_bft_mpq.put_nowait
    bft_from_server = lambda: server_bft_mpq.get(timeout=0.1)
    server_to_bft = server_bft_mpq.put_nowait

    #variables used to share state between processes
    client_ready = mpValue(c_bool, False)
    server_ready = mpValue(c_bool, False)
    net_ready = mpValue(c_bool, False)
    stop = mpValue(c_bool, False)
    bft_running = mpValue(c_bool, False)  # True = good network; False = bad network

    #print(my_address[1],my_address[0])
    net_server = NetworkServer(my_address[1], my_address[0], i, addresses, server_to_bft, server_ready, stop)
    net_client = NetworkClient(my_address[1], my_address[0], i, shard_id, N, addresses, client_from_bft, client_ready, stop,
                               bft_running, dynamic=True)


    # ================================================================================
    # Initialize node and join it to network
    # ================================================================================
    logg = set_consensus_log(i + shard_id * N)

    id = shard_id * N + i
    conn = sqlite3.connect(f'./node_dbs/node{id}.db' )
    cur = conn.cursor()
    cur.execute('DROP TABLE IF EXISTS txlist')
    TXs = read_pkl_file('./TXs')
    cur.execute('create table if not exists txlist (tx text primary key)')
    '''tx_cnt = 0
    for tx in TXs:
        input_shards, input_valids, output_shard, output_valid = parse_shard_info(tx)
        if len(input_shards) == 1 and input_shards[0] == output_shard and output_shard!=shard_id:
            continue
        else:
            cur.execute('insert into txlist (tx) values (?)', (tx,))
            tx_cnt += 1'''
    tmp = 0
    for j in range(tx_num):
        random.seed(time.time())
        if random.random() < 1 - Crate:
            tx = inter_tx_generator(250, shard_id)
        else:
            input_shards, input_valids, output_shard, output_valid = parse_shard_info(TXs[tmp])
            tmp += 1
            while shard_id not in input_shards:
                input_shards, input_valids, output_shard, output_valid = parse_shard_info(TXs[tmp])
                tmp += 1

            tx = TXs[tmp-1]
        cur.execute('insert into txlist (tx) values (?)', (tx,))
    conn.commit()

    bft = DumboBFTNode(sid, shard_id, i, B, shard_num, N, f, conn, bft_from_server, bft_to_client, net_ready, stop, logg, K, mute=False, debug=False, bft_running=bft_running)
    #bft = DumboBFTNode(sid, shard_id, i, B, shard_num, N, f, f'/home/lyn/BDT/TXs_file/TXs', bft_from_server,bft_to_client, net_ready, stop, K, mute=False, debug=False, bft_running=bft_running)

    net_server.start()
    net_client.start()


    while not client_ready.value or not server_ready.value:
        time.sleep(0.2)
        #print("waiting for self-node ready...")

    isfinish = 0
    ismodified = 0
    timestamp = datetime.now().timestamp() - 200
    ___send = lambda j, o:bft_to_client((j,o))

    def timestamp_broadcast():
        global isfinish,ismodified
        cnt = 0
        while isfinish != 1:
            time.sleep(0.1)
            cnt += 1
            if ismodified:
                #print("shard_id %d, node %d refresh timestamp, current start time %s" % (shard_id, i, datetime.utcfromtimestamp(timestamp).replace(tzinfo=timezone.utc)))
                logg.info("shard_id %d, node %d refresh timestamp, current start time %s" % (shard_id, i, datetime.utcfromtimestamp(timestamp).replace(tzinfo=timezone.utc)))
                ___send(-4,timestamp)
                ismodified = 0
                cnt = 0
            if cnt >= 30:
                cnt = 0
                ___send(-4, timestamp)


    gevent.spawn(timestamp_broadcast)
    time.sleep(1)

    #print('shard_id %d, node %d ready' % (shard_id, i))
    logg.info('shard_id %d, node %d ready' % (shard_id, i))


    #time_bias = fetch_time_bias()
    time_bias = 0
    #print('shard_id %d, node %d time_bias:%f' % (shard_id, i, time_bias))
    #logg.info('shard_id %d, node %d time_bias:%f' % (shard_id, i, time_bias))

    #time_bias = 0
    timestamp = datetime.now().timestamp() + 5 + time_bias

    #print('shard_id %d, node %d expected-start-time(global): %s %f' % (shard_id, i, datetime.utcfromtimestamp(timestamp).replace(tzinfo=timezone.utc), timestamp))
    logg.info('shard_id %d, node %d expected-start-time(global): %s %f' % (shard_id, i, datetime.utcfromtimestamp(timestamp).replace(tzinfo=timezone.utc), timestamp))

    #print(timestamp)
    ___send(-4,timestamp)
    #print(type(float(server_bft_mpq.get()[0])))
    #print(type(timestamp))
    #print(datetime.now(eastern).timestamp()>timestamp-8)

    while datetime.now().timestamp()+time_bias<timestamp:
        #print("node ",i," now time: ",datetime.now(eastern).timestamp())
        #print("node ",i," break time: ", timestamp)
        #print(datetime.now(eastern).timestamp()<timestamp)
        #print(datetime.now().timestamp()+time_bias,timestamp)
        try:
            #thing = server_bft_mpq.get_nowait()
            #print(thing)
            thing = float(server_bft_mpq.get_nowait()[1])
            #print(datetime.utcfromtimestamp(thing).replace(tzinfo=timezone.utc))
            if thing>timestamp:
                timestamp = thing
                ismodified = 1
            #print("node ",i," new break time: ",timestamp)
            #time.sleep(1)
        except Exception as e:
            time.sleep(0.1)

    isfinish = 1

    #print('shard_id %d, node %d final negotiated start time: %s %f' % (shard_id, i, datetime.utcfromtimestamp(timestamp).replace(tzinfo=timezone.utc), timestamp))
    logg.info('shard_id %d, node %d final negotiated start time: %s %f' % (shard_id, i, datetime.utcfromtimestamp(timestamp).replace(tzinfo=timezone.utc), timestamp))

    timestamp = datetime.now().timestamp()+time_bias

    #print('shard_id %d, node %d final start time: %s %f' % (shard_id, i, datetime.utcfromtimestamp(timestamp).replace(tzinfo=timezone.utc), timestamp))
    logg.info('shard_id %d, node %d final start time: %s %f' % (shard_id, i, datetime.utcfromtimestamp(timestamp).replace(tzinfo=timezone.utc), timestamp))
    with net_ready.get_lock():
        net_ready.value = True



    start = time.time()
    for j in range(R):
        logg.info('shard_id %d, node %d BFT round %d, start time %s' % (shard_id, i, j, datetime.utcfromtimestamp(timestamp).replace(tzinfo=timezone.utc)))
        print('shard_id %d, node %d BFT round %d, start time %s' % (shard_id, i, j, datetime.utcfromtimestamp(timestamp).replace(tzinfo=timezone.utc)))
        #print(f"shard_id {shard_id}, node {i} BFT round {j}")
        bft_thread = Greenlet(bft.run)
        bft_thread.start()
        bft_thread.join()

    time.sleep(2)
    with stop.get_lock():
        stop.value = True
        #print("shard_id ", shard_id, "node ",i," stop; total time:",time.time()-start - 2)
        total_time = time.time() - start - 2


    with open(f'log/consensus-node-{i + shard_id * N}.log','r') as f:
        content = f.read()
    round_pattern = r"breaks in (\d+\.\d+) seconds"
    round= re.findall(round_pattern,content)
    block_pattern = r"ACS Block Delay at Node \d+: (\d+\.\d+)"
    block = re.findall(block_pattern,content)

    round_numbers = [float(num) for num in round]
    block_numbers = [float(num) for num in block]
    round_delay = sum(round_numbers) / len(round_numbers)
    block_delay = sum(block_numbers) / len(block_numbers)

    latency = (1-Crate) * block_delay + Crate * (block_delay + round_delay)
    
    cur.execute('SELECT * FROM txlist')
    TXs = cur.fetchall()
    logg.info('shard_id %d node %d stop; total time: %f; total TPS: %f; average latency: %f' % (shard_id, i, total_time, (
                tx_num - len(TXs)) / total_time, latency))
    print('shard_id %d node %d stop; total time: %f; total TPS: %f; average latency: %f' % (shard_id, i, total_time, (
                tx_num - len(TXs)) / total_time, latency))
    logg.info('shard_id %d node %d stop; block_delay: %f ; round_delay: %f ; time-between-shards: %f' % (shard_id, i, block_delay,round_delay,round_delay-block_delay))
    print('shard_id %d node %d stop; block_delay: %f ; round_delay: %f ; time-between-shards: %f' % (shard_id, i, block_delay, round_delay, round_delay - block_delay))

    time.sleep(10)
    net_client.join()
    net_client.terminate()
    net_server.join()
    net_server.terminate()

