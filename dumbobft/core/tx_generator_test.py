import pickle
import time

#s=pickle.load(open('/home/lyn/BDT/TXs','rb'))
s=pickle.load(open('/home/lyn/BDT/TXs_file/TXs0','rb'))
print(len(s))
for i in s:
    print(i)
    time.sleep(2)