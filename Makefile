all:
	gcc -shared -fpic -O3 -march=native -o libdownconvert.so chirp_downconvert.c -pthread
	g++ -std=c++17 -Wall -O3 -march=native `pkg-config --cflags uhd hdf5 digital_rf` -o rx_uhd rx_uhd.cpp -pthread  -lboost_program_options -lboost_system -lboost_thread -lboost_date_time -lboost_regex -lboost_serialization -ldigital_rf `pkg-config --libs uhd hdf5 digital_rf`

apple:
	gcc -shared -fpic -O3 -march=native -o libdownconvert.so chirp_downconvert.c -pthread
	g++ -std=c++17 -Wall -O3 -march=native -o rx_uhd rx_uhd.cpp -pthread -lboost_program_options-mt -lboost_system-mt -lboost_thread-mt -lboost_date_time-mt -lboost_regex-mt -lboost_serialization-mt -ldigital_rf -lhdf5 -luhd -L/opt/local/lib -I/opt/local/include

convert:
	gcc -shared -fpic -O3 -march=native -o libdownconvert.so chirp_downconvert.c -pthread
