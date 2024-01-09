import os

host = "127.0.0.1"
port = 10000

with open('hosts.config', 'w') as hosts:
    for i in range(64):
        hosts.write(str(i) + " " + host + " " + host + " " + str(port) + os.linesep)
        port += 2500

# print("hosts.config is not correctly read... ")
# host = "127.0.0.1"
# port_base = int(rnd.random() * 5 + 1) * 10000
# addresses = [(host, port_base + 200 * i) for i in range(N)]
# print(addresses)