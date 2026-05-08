How to work with cluster / scripts

## 1. Start the cluster


./1_cluster_up.sh

- creates all VMs


## 2. Setup memcached


./2_setup_memcached.sh

- install all you need
!!! you can edit the memcached .conf file


## 3. Copy and (THEN) start scheduler


./3_copy_start_scheduler.sh

- copies scheduler code to the VM
- installs needed stuff again
- SSH into VM

it suggests:
sudo python3 scheduler.py


## 4. SSH scheduler and check logs

On the memcached VM:

./4_ssh_to_memcached.sh

- SSH to memcached

it suggests:
tail the log to see whats going on!


## 5. Start agent


./5_setup_agent.sh

- install all stuff
- auto SSH into agent VM

it suggests:
run mcperf to receive from measure and send actual traffic


## 6. Start measure (load generator)


./6_setup_measure.sh

- installs
- auto ssh

it suggest:
mcperf command




WITH  ./8_ssh_measure.sh

- auto ssh
- suggest to see the logs !