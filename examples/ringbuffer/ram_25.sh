#!/usr/bin/bash

#
# Realtime processing of a recording (25 MHz) of HF obtained using thor.py

# ensure the RAM storage is set, if not use:
#   mount -o remount,size=RINGBUFFER_SIZE /dev/shm
# ensure the output directory and logs directory are created
#   mkdir chirp2
#   mkdir logs

# program configuration
USRP_ADDRESS=192.168.10.14
SAMPLE_RATE=25e6
CENTER_FREQ=12.5e6
CONF_FILE=examples/ringbuffer/ram_25.ini

# path to the digital RF python tool Thor
THOR_PGRM_DIR=/home/chartat1/digital_rf/python/tools/

# ram disk buffer for fast i/o
RINGBUFFER_DIR=/dev/shm/hf25
# make this about a few GB more than a full sounder (25 GB)
RINGBUFFER_SIZE=32GB

# start receiving to a ringbuffer
rm -Rf $RINGBUFFER_DIR
python3 $THOR_PGRM_DIR/thor.py -m $USRP_ADDRESS -d A:A -c cha -f $CENTER_FREQ -r $SAMPLE_RATE $RINGBUFFER_DIR &
sleep 10

drf ringbuffer -z $RINGBUFFER_SIZE $RINGBUFFER_DIR -p 2 &
sleep 10

# how many CPU cores to use (do not allocate all cores to this program,
# leave some for thor.py and ringbuffer.py)
N_CPUS=8
# we use 2 cores for each mpi process when calculating an ionogram
N_CPUS_I=`expr $N_CPUS / 2`

# spawn the program scripts
mpirun -np $N_CPUS_I python3 calc_ionograms.py $CONF_FILE >logs/calc_ionograms.log 2>&1 &
python3 find_timings.py $CONF_FILE >logs/find_timings.log 2>&1 &
mpirun -np $N_CPUS python3 detect_chirps.py $CONF_FILE >logs/detect_chirps.log 2>&1 &
python3 plot_ionograms.py $CONF_FILE >logs/plot_ionograms.log 2>&1 &

# processes can be killed using:
#   pkill -f python3
#   pkill -f drf
#   rm -rf RINGBUFFER_DIR
#   rm -rf chirp2/ logs/