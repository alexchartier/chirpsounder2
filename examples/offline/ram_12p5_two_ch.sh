#!/usr/bin/bash

#
# Off-line processing of a recording (12.5 MHz) of HF obtained using thor.py

# ensure the RAM storage is set, if not use:
#   mount -o remount,size=32G /dev/shm

# program configuration
CONF_FILE=examples/offline/ram_12p5_two_ch.ini

# how many CPU cores to use (can be all since this is the only program running)
N_CPUS=2
# we use 2 cores for each mpi process when calculating an ionogram
N_CPUS_I=`expr $N_CPUS / 2`

# spawn the program scripts
mpirun -np $N_CPUS_I python3 calc_ionograms.py $CONF_FILE >logs/calc_ionograms.log 2>&1 &
python3 find_timings.py $CONF_FILE >logs/find_timings.log 2>&1 &
mpirun -np $N_CPUS python3 detect_chirps.py $CONF_FILE >logs/detect_chirps.log 2>&1 &
python3 plot_ionograms.py $CONF_FILE >logs/plot_ionograms.log 2>&1 &

# processes can be killed using:
#   pkill -f python3