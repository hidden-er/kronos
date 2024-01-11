import json
import logging
import os
import pickle
import re
import sqlite3
from gevent import monkey

from bdtbft.core.tx_generator import inter_tx_generator; monkey.patch_all(thread=False)

import time
import random
import traceback
from typing import List, Callable
from gevent import Greenlet
from myexperiements.sockettest.rotatinghotstuff_node import RotatingHotstuffBFTNode
from network.socket_server import NetworkServer
from network.socket_client import NetworkClient
from multiprocessing import Value as mpValue, Queue as mpQueue
from ctypes import c_bool

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
    parser.add_argument('--T', metavar='T', required=False,
                        help='fast path timeout', type=float, default=1)
    parser.add_argument('--P', metavar='P', required=False,
                        help='protocol to execute', type=str, default="mule")
    parser.add_argument('--M', metavar='M', required=False,
                        help='whether to mute a third of nodes', type=bool, default=False)
    parser.add_argument('--F', metavar='F', required=False,
                        help='batch size of fallback path', type=int, default=100)
    parser.add_argument('--D', metavar='D', required=False,
                        help='whether to debug mode', type=bool, default=False)
    parser.add_argument('--O', metavar='O', required=False,
                        help='whether to omit the fast path', type=bool, default=False)
    args = parser.parse_args()

    # Some parameters
    sid = args.sid
    i = args.id
    shard_id = args.shard_id
    tx_num = args.tx_num
    shard_num = args.shard_num
    N = args.N
    f = args.f
    B = args.B
    K = args.K
    R = args.R
    S = args.S
    T = args.T
    P = args.P
    M = args.M
    F = args.F
    D = args.D
    O = args.O

    # Random generator
    rnd = random.Random(sid)

    # Nodes list
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

    # bft_from_server, server_to_bft = mpPipe(duplex=True)
    # client_from_bft, bft_to_client = mpPipe(duplex=True)

    client_bft_mpq = mpQueue()
    #client_from_bft = client_bft_mpq.get
    client_from_bft = lambda: client_bft_mpq.get(timeout=0.00001)
    bft_to_client = client_bft_mpq.put_nowait

    server_bft_mpq = mpQueue()
    #bft_from_server = server_bft_mpq.get
    bft_from_server = lambda: server_bft_mpq.get(timeout=0.00001)
    server_to_bft = server_bft_mpq.put_nowait

    client_ready = mpValue(c_bool, False)
    server_ready = mpValue(c_bool, False)
    net_ready = mpValue(c_bool, False)
    stop = mpValue(c_bool, False)
    bft_running = mpValue(c_bool, False)  # True = good network; False = bad network

    net_server = NetworkServer(my_address[1], my_address[0], i, addresses, server_to_bft, server_ready, stop)
    net_client = NetworkClient(my_address[1], my_address[0], i, shard_id, N, addresses, client_from_bft, client_ready, stop,
                               bft_running, dynamic=False)
    
    logg = set_consensus_log(i + shard_id * N)

    id = shard_id * N + i
    conn = sqlite3.connect(f'./node_dbs/node{id}.db' )
    cur = conn.cursor()
    cur.execute('DROP TABLE IF EXISTS txlist')
    TXs = read_pkl_file('./TXs')
    cur.execute('create table if not exists txlist (tx text primary key)') 
    
    tmp = 0
    for j in range(tx_num):
        random.seed(time.time())
        if random.random() < 0.9:
            tx = inter_tx_generator(250, shard_id)
        else:
            tx = TXs[tmp]
            tmp += 1
        cur.execute('insert into txlist (tx) values (?)', (tx,))
    conn.commit()


    bft = RotatingHotstuffBFTNode(sid, shard_id, i, S, T, B, F, shard_num, N, f, conn, bft_from_server, bft_to_client, net_ready, stop, logg, K, mute=False, omitfast=False, bft_running=bft_running)
    #print(O)
    net_server.start()
    net_client.start()

    while not client_ready.value or not server_ready.value:
        time.sleep(1)
        print("waiting for network ready...")

    with net_ready.get_lock():
        net_ready.value = True
    print("network ready!!!")

    start = time.time()
    for j in range(R):
        logg.info('shard_id %d, node %d BFT round %d' % (shard_id, i, j))
        print(f"shard {shard_id}, node {i} BFT round {j}")
        bft_thread = Greenlet(bft.run)
        bft_thread.start()
        bft_thread.join()

    time.sleep(2)
    with stop.get_lock():
        stop.value = True
        #print("shard ", shard_id, "node ",i," stop; total time: ", time.time()-start)
        total_time = time.time()-start - 2


    with open(f'log/consensus-node-{i + shard_id * N}.log','r') as f:
        content = f.read()
    round_pattern = r"breaks in (\d+\.\d+) seconds"
    round= re.findall(round_pattern,content)
    block_pattern = r"Hotstuff Block Delay at Node \d+: (\d+\.\d+)"
    block = re.findall(block_pattern,content)

    round_numbers = [float(num) for num in round]
    block_numbers = [float(num) for num in block]
    round_delay = sum(round_numbers) / len(round_numbers)
    block_delay = sum(block_numbers) / len(block_numbers)

    num = 0.9
    latency = num * block_delay + (1 - num) * (block_delay + round_delay)

    cur.execute('SELECT * FROM txlist')
    TXs = cur.fetchall()
    logg.info('shard_id %d node %d stop; total time: %f; total TPS: %f; average latency: %f' % (shard_id, i, total_time, (
                tx_num - len(TXs)) / total_time, latency))
    print('shard_id %d node %d stop; total time: %f; total TPS: %f; average latency: %f' % (shard_id, i, total_time, (
                tx_num - len(TXs)) / total_time, latency))
    time.sleep(10)
    net_client.join()
    net_client.terminate()
    net_server.join()
    net_server.terminate()
