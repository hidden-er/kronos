# kronos

Proof-of-Concept implementation for a sharding asynchronous BFT consensus framework.

The code uses Dumbo-2 (Guo et al. CCS'2020) inside shards , and use loading pools to solve 

cross-slice collaboration issues. The code is forked from the implementation of BDT protocol, 

but actually it only uses Dumbo module and related Honeybadger part.

1. To run the benchmarks at your local machine (with Ubuntu 18.84 LTS), use env-batch.sh to  install all dependencies:
    ```shell
    sudo ./env-batch.sh
    ```
    
2. A quick start to run kronos can be:
   ```
   ./start.sh 3 4 1 1000 5 0 12 20000
   ```
   
   It means protocol has 3 shards , each shard has 4 nodes(include 1 fake node); batch 1000, round 5.

3. If you would like to test the code among AWS cloud servers (with Ubuntu 18.84 LTS). You can follow the commands inside /aws to remotely start the protocols at all servers. An example to conduct the WAN tests from your PC side terminal can be:
   ```
   sudo ./aws/aws-pre
   sudo ./aws/aws-run
   sudo ./aws/aws-log
   ```

​		note that aws-pre only need to run 1 time.





Here down below is the original README.md of HoneyBadgerBFT

# HoneyBadgerBFT
The Honey Badger of BFT Protocols.

<img width=200 src="http://i.imgur.com/wqzdYl4.png"/>

[![Travis branch](https://img.shields.io/travis/initc3/HoneyBadgerBFT-Python/dev.svg)](https://travis-ci.org/initc3/HoneyBadgerBFT-Python)
[![Codecov branch](https://img.shields.io/codecov/c/github/initc3/honeybadgerbft-python/dev.svg)](https://codecov.io/github/initc3/honeybadgerbft-python?branch=dev)

HoneyBadgerBFT is a leaderless and completely asynchronous BFT consensus protocols.
This makes it a good fit for blockchains deployed over wide area networks
or when adversarial conditions are expected.
HoneyBadger nodes can even stay hidden behind anonymizing relays like Tor, and
the purely-asynchronous protocol will make progress at whatever rate the
network supports.

This repository contains a Python implementation of the HoneyBadgerBFT protocol.
It is still a prototype, and is not approved for production use. It is intended
to serve as a useful reference and alternative implementations for other projects.

## Development Activities

Since its initial implementation, the project has gone through a substantial
refactoring, and is currently under active development.

At the moment, the following three milestones are being focused on:

* [Bounded Badger](https://github.com/initc3/HoneyBadgerBFT-Python/milestone/3)
* [Test Network](https://github.com/initc3/HoneyBadgerBFT-Python/milestone/2<Paste>)
* [Release 1.0](https://github.com/initc3/HoneyBadgerBFT-Python/milestone/1)

A roadmap of the project can be found in [ROADMAP.rst](./ROADMAP.rst).


### Contributing
Contributions are welcomed! To quickly get setup for development:

1. Fork the repository and clone your fork. (See the Github Guide
   [Forking Projects](https://guides.github.com/activities/forking/) if
   needed.)

2. Install [`Docker`](https://docs.docker.com/install/). (For Linux, see
   [Manage Docker as a non-root user](https://docs.docker.com/install/linux/linux-postinstall/#manage-docker-as-a-non-root-user)
   to run `docker` without `sudo`.)

3. Install [`docker-compose`](https://docs.docker.com/compose/install/).

4. Run the tests (the first time will take longer as the image will be built):

   ```bash
   $ docker-compose run --rm honeybadger
   ```

   The tests should pass, and you should also see a small code coverage report
   output to the terminal.

If the above went all well, you should be setup for developing
**HoneyBadgerBFT-Python**!

## License
This is released under the CRAPL academic license. See ./CRAPL-LICENSE.txt
Other licenses may be issued at the authors' discretion.
