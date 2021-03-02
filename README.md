<img src="https://github.com/amoretti86/phylo/blob/master/data/figures/primatesTVCSMC_5.png"
     alt="VCSMC Figure"
     style="float: left; margin-right: 10px;" />


Type in terminal: 

`python runner.py 
   --dataset=[some_data] 
   --n_particles=[some_number]
   --batch_size=[some_number]
   --learning_rate=[some_number]
   --twisting=[true/false]
   --jcmodel=[true/false]
   --num_epoch=100`   

This runner.py file assumes that all datasets (`primate.p`, for example) are directly put under a folder called 'data'
