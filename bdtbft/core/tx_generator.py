import random,string,time
import pickle
def tx_generator(size=250, shard_num=4, chars=string.ascii_uppercase + string.digits):
    random.seed(time.time())
    # 90% probability: input and output shards are the same
    if random.random() < 0.9:
        common_shard = random.randint(0, shard_num - 1)
        input_valid = random.choice([0, 1])
        #shard_info = f", Input Shard: {str([common_shard])}, Input Valid: {str([input_valid])}, Output Shard: {common_shard}, Output Valid: {1}"
        shard_info = f", Input Shard: {str([common_shard])}, Input Valid: [1], Output Shard: {common_shard}, Output Valid: {1}"

    # 10% probability: input and output shards are different
    else:
        #input_shard_num = random.randint(1, shard_num-1)
        if shard_num >= 4:
            input_shard_num = random.randint(1, 3)
        else:
            input_shard_num = 1
        input_shards = sorted(random.sample(range(0, shard_num), input_shard_num))
        input_valid = random.choices([0, 1],weights=[20, 80],k=input_shard_num)
        output_shard = random.choice([shard for shard in range(shard_num) if shard not in input_shards])
        shard_info = f", Input Shard: {str(input_shards)}, Input Valid: {str(input_valid)}, Output Shard: {output_shard}, Output Valid: {0}"

    random_string = ''.join(random.choice(chars) for _ in range(size - 10))

    return f'<Dummy TX: {random_string}{shard_info} >'

def inter_tx_generator(size, shard_id, chars=string.ascii_uppercase + string.digits):
    random.seed(time.time())

    shard_info = f", Input Shard: {str([shard_id])}, Input Valid: [1], Output Shard: {shard_id}, Output Valid: {1}"
    random_string = ''.join(random.choice(chars) for _ in range(size - 10))

    return f'<Dummy TX: {random_string}{shard_info} >'

def cross_tx_generator(size, shard_num, chars=string.ascii_uppercase + string.digits):
    random.seed(time.time())

    if shard_num >= 4:
        input_shard_num = random.randint(1, 3)
    else:
        input_shard_num = 1
    input_shards = sorted(random.sample(range(0, shard_num), input_shard_num))
    input_valid = random.choices([0, 1],weights=[20, 80],k=input_shard_num)
    output_shard = random.choice([shard for shard in range(shard_num) if shard not in input_shards])
    shard_info = f", Input Shard: {str(input_shards)}, Input Valid: {str(input_valid)}, Output Shard: {output_shard}, Output Valid: {0}"
    random_string = ''.join(random.choice(chars) for _ in range(size - 10))

    return f'<Dummy TX: {random_string}{shard_info} >'


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--shard_num', metavar='shard_num', required=True,
                        help='number of shards', type=int)
    parser.add_argument('--tx_num', metavar='tx_num', required=True,
                        help='number of txs', type=int)

    args = parser.parse_args()
    #print(args.shard_num)
    N = args.tx_num
    txs=[]
    for i in range(N):
        tx = cross_tx_generator(250, args.shard_num)
        txs.append(tx)
        #print(tx)
        #time.sleep(1)

    with open('TXs','wb') as f:
        pickle.dump(txs,f)

    #tx_generator_test()