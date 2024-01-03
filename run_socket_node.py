import sqlite3
from gevent import monkey;

monkey.patch_all(thread=False)

import time
import random
import logging
import os
import pickle
import re

from gevent import Greenlet
from myexperiements.sockettest.dumbo_node import DumboBFTNode
from network.socket_server import NetworkServer
from network.socket_client import NetworkClient
from multiprocessing import Value as mpValue, Queue as mpQueue
from ctypes import c_bool

server_bft_mpq = mpQueue()

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
    parser.add_argument('--shard_num', metavar='shard_num', required=True,
                        help='number of shards', type=int)
    parser.add_argument('--N', metavar='N', required=True,
                        help='number of parties', type=int)
    parser.add_argument('--f', metavar='f', required=True,
                        help='number of faulties', type=int)
    parser.add_argument('--B', metavar='B', required=True,
                        help='size of batch', type=int)
    parser.add_argument('--K', metavar='K', required=True,
                        help='rounds to execute', type=int)
    parser.add_argument('--S', metavar='S', required=False,
                        help='slots to execute', type=int, default=50)
    parser.add_argument('--M', metavar='M', required=False,
                        help='whether to mute a third of nodes', type=bool, default=False)
    parser.add_argument('--D', metavar='D', required=False,
                        help='whether to debug mode', type=bool, default=False)
    args = parser.parse_args()

    sid, i, shard_id, shard_num, N, f, B, K, S, M, D = (
        args.sid, args.id, args.shard_id, args.shard_num, args.N, args.f, args.B, args.K, args.S, args.M, args.D)
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
    for tx in TXs:
        cur.execute('insert into txlist (tx) values (?)', (tx,))
    conn.commit()

    bft = DumboBFTNode(sid, shard_id, i, B, shard_num, N, f, conn, bft_from_server, bft_to_client, net_ready, stop, logg, K, mute=False, debug=False, bft_running=bft_running)
    #bft = DumboBFTNode(sid, shard_id, i, B, shard_num, N, f, f'/home/lyn/BDT/TXs_file/TXs', bft_from_server,bft_to_client, net_ready, stop, K, mute=False, debug=False, bft_running=bft_running)

    net_server.start()
    net_client.start()

    while not client_ready.value or not server_ready.value:
        time.sleep(1)
        print("waiting for network ready...")

    with net_ready.get_lock():
        net_ready.value = True
    print("network ready!!!")


    start = time.time()
    for j in range(5):
        logg.info('shard_id %d, node %d BFT round %d' % (shard_id, i, j))
        print('shard_id %d, node %d BFT round %d' % (shard_id, i, j))
        #print(f"shard_id {shard_id}, node {i} BFT round {j}")
        bft_thread = Greenlet(bft.run)
        bft_thread.start()
        bft_thread.join()

    time.sleep(2)
    with stop.get_lock():
        stop.value = True
        #print("shard_id ", shard_id, "node ",i," stop; total time:",time.time()-start - 2)
        total_time = time.time()-start - 2


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

    num = 0.9
    latency = num * block_delay + (1 - num) * (block_delay + round_delay)
    
    cur.execute('SELECT * FROM txlist')
    TXs = cur.fetchall()
    logg.info('shard_id %d node %d stop; total time: %f; total TPS: %f; average latency: %f' % (shard_id, i, total_time, (
                20000 - len(TXs)) / total_time, latency))
    print('shard_id %d node %d stop; total time: %f; total TPS: %f; average latency: %f' % (shard_id, i, total_time, (
                20000 - len(TXs)) / total_time, latency))

    time.sleep(10)
    net_client.join()
    net_client.terminate()
    net_server.join()
    net_server.terminate()

