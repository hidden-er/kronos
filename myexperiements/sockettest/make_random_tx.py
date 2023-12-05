import string
import random

def tx_generator1(size=250, shard_num=4, chars=string.ascii_uppercase + string.digits):
    # add input parse: shard_num
    # add output information: Input Shard \ Output Shard
    shard_info = f", Input Shard: {[random.randint(0, shard_num - 1) for _ in range(random.randint(1, shard_num))]}, Output Shard: {random.randint(0, shard_num - 1)}"
    random_string = ''.join(random.choice(chars) for _ in range(size - 10))

    return f'<Dummy TX: {random_string}{shard_info} Finished>'
    #return '<Dummy TX: ' + ''.join(random.choice(chars) for _ in range(size - 10)) + '>'


def tx_generator(size=250, shard_num=4, chars=string.ascii_uppercase + string.digits):
    # 90% probability: input and output shards are the same
    if random.random() < 0.9:
        common_shard = random.randint(1, shard_num)
        input_valid = random.choice([0, 1])
        shard_info = f", Input Shard: {str([common_shard])}, Input Valid: {str([input_valid])}, Output Shard: {common_shard}, Output Valid: {0}"
    # 10% probability: input and output shards are different
    else:
        input_shard_num = random.randint(1, shard_num - 1)
        input_shards = sorted(random.sample(range(1, shard_num + 1), input_shard_num))
        input_valid = [random.choice([0, 1]) for _ in range(input_shard_num)]
        output_shard = random.choice([shard for shard in range(shard_num) if shard not in input_shards])
        shard_info = f", Input Shard: {str(input_shards)}, Input Valid: {str(input_valid)}, Output Shard: {output_shard}, Output Valid: {0}"

    random_string = ''.join(random.choice(chars) for _ in range(size - 10))
    
    return f'<Dummy TX: {random_string}{shard_info} >'




generated_tx = tx_generator(250, 4)
print(generated_tx)

