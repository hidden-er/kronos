from gevent import monkey; monkey.patch_all(thread=False)

import traceback
import time
from collections import defaultdict
from gevent.event import Event
from gevent.queue import Queue
from gevent import Timeout
from crypto.ecdsa.ecdsa import ecdsa_sign, ecdsa_vrfy, PublicKey
import os
import json
import gevent
import hashlib, pickle


def hash(x):
    return hashlib.sha256(pickle.dumps(x)).digest()


def hsfastpath(sid, shard_id, pid, N, f, leader, tx_to_send, output_notraized_block, Snum, Bsize, Tout, hash_genesis, PK2s, SK2, recv, send, omitfast=False, logger=None):
    """Fast path, Byzantine Safe Broadcast
    :param str sid: ``the string of identifier``
    :param int pid: ``0 <= pid < N``
    :param int N:  at least 3
    :param int f: fault tolerance, ``N >= 3f + 1``
    :param int leader: the pid of leading node
    :param get_input: a function to get input TXs, e.g., input() to get a transaction
    :param output_notraized_block: a function to deliver notraized blocks, e.g., output(block)

    :param list PK2s: an array of ``coincurve.PublicKey'', i.e., N public keys of ECDSA for all parties
    :param PublicKey SK2: ``coincurve.PrivateKey'', i.e., secret key of ECDSA
    :param float Tout: timeout of a slot
    :param int Snum: number of slots in a epoch
    :param int Bsize: batch size, i.e., the number of TXs in a batch
    :param hash_genesis: the hash of genesis block
    :param recv: function to receive incoming messages
    :param send: function to send outgoing messages
    :param bcast: function to broadcast a message
    :return tuple: False to represent timeout, and True to represent success
    """

    TIMEOUT = Tout
    SLOTS_NUM = Snum
    BATCH_SIZE = Bsize

    assert leader in range(N)
    slot_cur = 1

    hash_prev = hash_genesis
    pending_block = None
    notraized_block = None

    # Leader's temp variables
    voters = defaultdict(lambda: set())
    votes = defaultdict(lambda: dict())
    decides = defaultdict(lambda: Queue(1))

    decide_sent = [False] * (SLOTS_NUM + 3)  # The first item of the list is not used

    def handle_messages():
        nonlocal leader, hash_prev, pending_block, notraized_block, voters, votes, slot_cur

        while True:

            #gevent.sleep(0)

            (sender, msg) = recv()
            #logger.info("receving a fast path msg " + str((sender, msg)))

            if msg[0] == 'VOTE' and pid == leader and len(voters[slot_cur]) < N - f:

                _, slot, hash_p, sig_p = msg
                #_, slot, hash_p, raw_sig_p, tx_batch, tx_sig = msg
                #sig_p = deserialize1(raw_sig_p)

                if sender not in voters[slot]:

                    try:
                        assert slot == slot_cur
                    except AssertionError:
                        if logger is not None:
                            # logger.info("vote out of sync from node %d" % sender)
                            pass
                        #if slot < slot_cur:
                        #    if logger is not None: logger.info("Late vote from node %d! Not needed anymore..." % sender)
                        #else:
                        #    if logger is not None: logger.info("Too early vote from node %d! I do not decide earlier block yet..." % sender)
                        # msg_noncritical_signal.set()
                        continue

                    try:
                        assert hash_p == hash_prev
                    except AssertionError:
                        if logger is not None:
                            logger.info("False vote from node %d though within the same slot!" % sender)
                        # msg_noncritical_signal.set()
                        continue

                    try:
                        assert ecdsa_vrfy(PK2s[sender % N], hash_p, sig_p)
                    except AssertionError:
                        if logger is not None:
                            logger.info("Vote signature failed!")
                        # msg_noncritical_signal.set()
                        continue


                    voters[slot_cur].add(sender)
                    votes[slot_cur][sender] = sig_p

                    if len(voters[slot_cur]) == N - f and not decide_sent[slot_cur]:
                        #print(slot_cur)
                        Sigma = tuple(votes[slot_cur].items())
                        tx_batch = json.dumps(tx_to_send)

                        send(-2, ('DECIDE', slot_cur, hash_prev, Sigma, tx_batch))
                        #if logger is not None: logger.info("Decide made and sent")
                        decide_sent[slot_cur] = True
                        decides[slot_cur].put_nowait((hash_p, Sigma, tx_batch))

            if msg[0] == "DECIDE" and pid != leader:

                _, slot, hash_p, Sigma_p, batches = msg

                try:
                    assert slot == slot_cur
                except AssertionError:
                    if logger is not None:
                        # logger.info("Out of synchronization")
                        pass
                    # msg_noncritical_signal.set()
                    continue

                try:
                    assert len(Sigma_p) >= N - f
                except AssertionError:
                    if logger is not None:
                        logger.info("No enough ecdsa signatures!")
                    # msg_noncritical_signal.set()
                    continue

                try:
                    for item in Sigma_p:
                        #print(Sigma_p)
                        (sender, sig_p) = item
                        assert ecdsa_vrfy(PK2s[sender % N], hash_p, sig_p)
                except AssertionError:
                    if logger is not None:
                        logger.info("ecdsa signature failed!")
                    # msg_noncritical_signal.set()
                    continue

                decides[slot_cur].put_nowait((hash_p, Sigma_p, batches))

    """
    One slot
    """

    def one_slot():
        nonlocal shard_id, N, slot_cur, hash_prev

        try:
            sig_prev = ecdsa_sign(SK2, hash_prev)
            send(leader + shard_id * N, ('VOTE', slot_cur, hash_prev, sig_prev))
        except AttributeError as e:
            if logger is not None:
                logger.info(traceback.print_exc())

        #print('4')

        (h_p, Sigma_p, batches) = decides[slot_cur].get()  # Block to wait for the voted block


        pending_block = (sid, slot_cur, h_p, Sigma_p, batches)
        pending_block_header = (sid, slot_cur, h_p, hash(batches))
        hash_prev = hash(pending_block_header)

        notraized_block = (pending_block[0], pending_block[1], pending_block[2], pending_block[4])

        output_notraized_block(notraized_block)


    """
    Execute the slots
    """

    recv_thread = gevent.spawn(handle_messages)
    #gevent.sleep(0)

    one_slot()
