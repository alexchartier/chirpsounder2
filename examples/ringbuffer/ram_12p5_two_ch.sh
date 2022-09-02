#!/usr/bin/bash

#
# Realtime processing of a recording (25 MHz) of HF obtained using thor.py

# ensure the RAM storage is set, if not use:
#   mount -o remount,size=RINGBUFFER_SIZE /dev/shm

# path to the program files
PGRM_DIR=/home/chartat1/chirpsounder2_zach

# program configuration
USRP_ADDRESS=192.168.10.14
SAMPLE_RATE=12.5e6
CENTER_FREQ=13.75e6
CONF_FILE=$PGRM_DIR/ringbuffer/ram_12p5_two_ch.ini
OUTPUT_DIR=./chirp2
LOG_DIR=./logs

# path to the digital RF python tool Thor
THOR_PGRM_DIR=/home/chartat1/digital_rf/python/tools

# ram disk buffer for fast i/o
RINGBUFFER_DIR=/dev/shm/hf25
RINGBUFFER_STAGING_DIR=/dev/shm/hf25_staging
# make this about a few GB more than a full sounder (25 GB)
RINGBUFFER_SIZE=52GB

# start receiving to a ringbuffer
rm -Rf $RINGBUFFER_DIR $RINGBUFFER_STAGING_DIR
python3 $THOR_PGRM_DIR/thor.py -m $USRP_ADDRESS -d "A:A A:B" -c cha,chb -f $CENTER_FREQ -r $SAMPLE_RATE $RINGBUFFER_DIR &
sleep 10

drf ringbuffer -z $RINGBUFFER_SIZE $RINGBUFFER_DIR -p 2 &
sleep 10

# how many CPU cores to use (do not allocate all cores to this program,
# leave some for thor.py and ringbuffer.py)
N_CPUS=4
# we use 2 cores for each mpi process when calculating an ionogram
N_CPUS_I=`expr $N_CPUS / 2`

# spawn the program scripts
mkdir -p $OUTPUT_DIR $LOG_DIR
mpirun -np $N_CPUS_I python3 $PGRM_DIR/calc_ionograms.py $CONF_FILE >logs/calc_ionograms.log 2>&1 &
python3 $PGRM_DIR/find_timings.py $CONF_FILE >logs/find_timings.log 2>&1 &
mpirun -np $N_CPUS python3 $PGRM_DIR/detect_chirps.py $CONF_FILE >logs/detect_chirps.log 2>&1 &
python3 $PGRM_DIR/plot_ionograms.py $CONF_FILE >logs/plot_ionograms.log 2>&1 &

# processes can be killed using:
#   pkill -f python3
#   pkill -f drf
#   rm -rf RINGBUFFER_DIR
#   rm -rf chirp2/ logs/