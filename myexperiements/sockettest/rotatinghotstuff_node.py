import re
from gevent import monkey; monkey.patch_all(thread=False)

import random
from typing import Callable
import os
import pickle
from gevent import time, Greenlet
from bdtbft.core.rotatinghotstuff import RotatingLeaderHotstuff
from coincurve import PrivateKey, PublicKey
from multiprocessing import Value as mpValue
from ctypes import c_bool
from multiprocessing import Lock


lock = Lock()


def load_key(id, N):
    with open(os.getcwd() + '/keys' + '/' + 'sPK.key', 'rb') as fp:
        sPK = pickle.load(fp)

    with open(os.getcwd() + '/keys' + '/' + 'sPK1.key', 'rb') as fp:
        sPK1 = pickle.load(fp)

    sPK2s = []
    for i in range(N):
        with open(os.getcwd() + '/keys' + '/' + 'sPK2-' + str(i) + '.key', 'rb') as fp:
            sPK2s.append(PublicKey(pickle.load(fp)))

    with open(os.getcwd() + '/keys' + '/' + 'ePK.key', 'rb') as fp:
        ePK = pickle.load(fp)

    with open(os.getcwd() + '/keys' + '/' + 'sSK-' + str(id) + '.key', 'rb') as fp:
        sSK = pickle.load(fp)

    with open(os.getcwd() + '/keys' + '/' + 'sSK1-' + str(id) + '.key', 'rb') as fp:
        sSK1 = pickle.load(fp)

    with open(os.getcwd() + '/keys' + '/' + 'sSK2-' + str(id) + '.key', 'rb') as fp:
        sSK2 = PrivateKey(pickle.load(fp))

    with open(os.getcwd() + '/keys' + '/' + 'eSK-' + str(id) + '.key', 'rb') as fp:
        eSK = pickle.load(fp)

    return sPK, sPK1, sPK2s, ePK, sSK, sSK1, sSK2, eSK


def read_pkl_file(file_path):
    while True:
        try:
            with lock:
                with open(file_path, 'rb') as f:
                    data = pickle.load(f)
            # with open(file_path, 'rb') as f:
            # data = pickle.load(f))
            return data
        except:
            continue


def write_pkl_file(data, file_path):
    with lock:
        with open(file_path, 'wb') as f:
            pickle.dump(data, f)


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


class RotatingHotstuffBFTNode (RotatingLeaderHotstuff):

    def __init__(self, sid, shard_id, id, S, T, Bfast, Bacs, shard_num, N, f, TXs_file_path: str, bft_from_server: Callable, bft_to_client: Callable, ready: mpValue, stop: mpValue, logg, K=3, mode='debug', mute=False, bft_running: mpValue=mpValue(c_bool, True), omitfast=False):
        self.sPK, self.sPK1, self.sPK2s, self.ePK, self.sSK, self.sSK1, self.sSK2, self.eSK = load_key(id, N)
        #self.recv_queue = recv_q
        #self.send_queue = send_q
        self.bft_from_server = bft_from_server
        self.bft_to_client = bft_to_client
        self.ready = ready
        self.stop = stop
        self.mode = mode
        self.running = bft_running
        self.TXs = TXs_file_path

        RotatingLeaderHotstuff.__init__(self, sid, shard_id, id, S, T, max(int(Bfast), 1), max(int(Bacs/N), 1), shard_num, N, f, self.sPK, self.sSK, self.sPK1, self.sSK1, self.sPK2s, self.sSK2, self.ePK, self.eSK, send=None, recv=None, logg=logg, K=K, mute=mute, omitfast=omitfast)

    def prepare_bootstrap(self):
        #self.logger.info('node id %d is inserting dummy payload TXs' % (self.id))
        if self.mode == 'test' or 'debug':  # K * max(Bfast * S, Bacs)
            cur = self.TXs.cursor()
            cur.execute('SELECT * FROM txlist')
            TXs = cur.fetchall()
            self.logger.info('node %d in shard %d before extract batch has %d TXS' % (self.id, self.shard_id, len(TXs)))
            print('node %d in shard %d before extract batch has %d TXS' % (self.id, self.shard_id, len(TXs)))
            k = 0
            for tx in TXs:
                cur.execute('DELETE FROM txlist WHERE tx=?', (tx[0],))

                RotatingLeaderHotstuff.submit_tx(self, tx[0])
                k += 1
                if k == self.FAST_BATCH_SIZE:
                    break
            
            self.TXs.commit()
            cur.execute('SELECT * FROM txlist')
            TXs = cur.fetchall()
            self.logger.info('node %d in shard %d after extract batch has %d TXS' % (self.id, self.shard_id, len(TXs)))
            print('node %d in shard %d after extract batch has %d TXS' % (self.id, self.shard_id, len(TXs)))
            #write_pkl_file(TXs,self.TXs)
        else:
            pass

            # TODO: submit transactions through tx_buffer
        ##print(self.transaction_buffer.queue)
        self.logger.info('node id %d completed the loading of dummy TXs' % (self.id))

    def run(self):

        pid = os.getpid()
        #self.logger.info('node %d\'s starts to run consensus on process id %d' % (self.id, pid))
        self.logger.info('parameters: N=%d, f=%d, S=%d, T=%d, fast-batch=%d, acs-batch=%d, K=%d, O=%d' % (self.N, self.f, self.SLOTS_NUM, self.TIMEOUT, self.FAST_BATCH_SIZE, self.FALLBACK_BATCH_SIZE, self.K, self.omitfast))

        self._send = lambda j, o: self.bft_to_client((j, o))
        self._recv = lambda: self.bft_from_server()

        self.prepare_bootstrap()

        while not self.ready.value:
            time.sleep(1)

        self.running.value = True

        self.run_bft()
        #self.stop.value = True


def main(sid, i, S, T, B, N, f, addresses, K):
    mule = RotatingHotstuffBFTNode(sid, i, S, T, B, N, f, addresses, K)
    mule.run_bft()


if __name__ == '__main__':

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--sid', metavar='sid', required=True,
                        help='identifier of node', type=str)
    parser.add_argument('--id', metavar='id', required=True,
                        help='identifier of node', type=int)
    parser.add_argument('--N', metavar='N', required=True,
                        help='number of parties', type=int)
    parser.add_argument('--f', metavar='f', required=True,
                        help='number of faulties', type=int)
    parser.add_argument('--B', metavar='B', required=True,
                        help='size of batch', type=int)
    parser.add_argument('--K', metavar='K', required=True,
                        help='rounds to execute', type=int)
    args = parser.parse_args()

    # Some parameters
    sid = args.sid
    i = args.id
    N = args.N
    f = args.f
    B = args.B
    K = args.K

    # Epoch Setup
    S = 50
    T = 0.05  # Timeout

    # Random generator
    rnd = random.Random(sid)

    # Nodes list
    host = "127.0.0.1"
    port_base = int(rnd.random() * 5 + 1) * 10000
    addresses = [(host, port_base + 200 * i) for i in range(N)]
    print(addresses)

    main(sid, i, S, T, B, N, f, addresses, K)
