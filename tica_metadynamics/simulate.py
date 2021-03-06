#!/bin/env python
from mpi4py import MPI
import argparse
from msmbuilder.utils import load
from .utils import get_gpu_index
import socket
import numpy as np
import glob
from simtk.unit import *
from .plumed_writer import get_plumed_dict
import os
import mdtraj as md 
from simtk.openmm.app import *
from simtk.openmm import *
boltzmann_constant = 0.0083144621

comm = MPI.COMM_WORLD
size = comm.Get_size()
rank = comm.Get_rank()


def swap_with_msm_state(sim_obj, swap_folder,force_group,beta):
    flist = glob.glob(os.path.join(swap_folder,"state*.xml"))
    print("Found %d states"%len(flist), flush=True)
    random_chck = np.random.choice(flist)
    print("Attempting swap with %s"%random_chck, flush=True)
    old_state=sim_obj.context.getState(getPositions=True, getVelocities=True,\
        getForces=True,getEnergy=True,getParameters=True,enforcePeriodicBox=True)

    old_energy = sim_obj.context.getState(getEnergy=True,groups={force_group}).\
            getPotentialEnergy().value_in_unit(kilojoule_per_mole)

    new_state = XmlSerializer.deserialize(open(random_chck).read())
    sim_obj.context.setState(new_state)
    new_energy = sim_obj.context.getState(getEnergy=True,groups={force_group}).\
            getPotentialEnergy().value_in_unit(kilojoule_per_mole)
    #if new_e < old_e , delta e is >0 and p ==1
    delta_e = old_energy - new_energy
    probability = np.min((1,np.exp(beta*delta_e)))
    accept = np.random.random() < probability
    if accept:
        print("Swap accepted with %s"%random_chck)
    else:
        #reset back to old_state
        sim_obj.context.setState(old_state)
    return sim_obj


class TicaSimulator(object):
    def __init__(self, file_loc="metad_sim.pkl"):
        from tica_metadynamics.load_sim import create_simulation
        self.file_loc = file_loc
        self.metad_sim = load(self.file_loc)
        self.beta = 1/(boltzmann_constant * self.metad_sim.temp)

        #get
        self.rank = rank
        self.size = size
        self.host_name = socket.gethostname()
        self.gpu_index = get_gpu_index()

        #setup MSM swap stuff
        if self.metad_sim.msm_swap_folder is not None:
            self.setup_msm_swap()

        print("Hello from rank %d running tic %d on "
          "host %s with gpu %d"%(self.rank, self.rank,
                                 self.host_name, self.gpu_index))
        # if multi walkers
        if hasattr(self.metad_sim,"n_walkers") and self.metad_sim.n_walkers > 1:
            cbd = self.metad_sim.base_dir
            walker_index = int(os.path.split(cbd)[1].strip("walker_"))
            print("I am walker %d running tic%d"%(walker_index,self.rank))
            self.metad_sim.walker_index = walker_index

        if self.metad_sim.plumed_dict is not None:
            self.plumed_force_dict = self.metad_sim.plumed_dict
        else:
            self.plumed_force_dict = get_plumed_dict(self.metad_sim)

        # last replica is the neutral replica
        if self.metad_sim.neutral_replica and self.rank==self.size-1:
            from tica_metadynamics.load_sim import create_neutral_simulation
            self.sim_obj = create_neutral_simulation(self.metad_sim.base_dir,
                                                     self.metad_sim.starting_coordinates_folder,
                                                     self.gpu_index,
                                                     self.metad_sim.sim_save_rate,
                                                     self.metad_sim.platform)
        else:
            self.sim_obj, self.force_group = create_simulation(self.metad_sim.base_dir,
                                                           self.metad_sim.starting_coordinates_folder,
                                                           self.gpu_index,
                                                           self.rank,
                                                           self.plumed_force_dict[self.rank],
                                                           self.metad_sim.sim_save_rate,
                                                           self.metad_sim.platform)
        if self.rank ==0 and self.size > 1:
            self.log_file = open("../swap_log.txt","a")
            header = ["Iteration","S_i","S_j","Eii","Ejj","Eij","Eji",
                      "DeltaE","Temp","Beta","Probability","Accepted"]
            self.log_file.writelines("#{}\t{}\t{}\t{}\t{}\t{}"
                                "\t{}\t{}\t{}\t{}\t{}\t{}\n".format(*header))

    def setup_msm_swap(self):
        self.full_list =  glob.glob(os.path.join(self.metad_sim.msm_swap_folder,"state*.xml"))
        if self.metad_sim.msm_swap_scheme == 'random':
            pass
        elif self.metad_sim.msm_swap_scheme == 'swap_once':
            self._tabu_list=[]
        elif self.metad_sim.msm_swap_scheme in ['tabu_list','min_count','wt_msm']:
            self.featurizer = self.metad_sim.featurizer
            self.tica_mdl = self.metad_sim.tica_mdl
            self.kmeans_mdl  = self.metad_sim.kmeans_mdl
            self.nrm = self.metad_sim.nrm
            self.top = md.load(os.path.join(self.metad_sim.starting_coordinates_folder,"0.pdb"))
            self.known_msm_states = {}
            for i in self.full_list:
                state = XmlSerializer.deserialize(open(i).read())
                self.top.xyz=np.array(state.getPositions()/nanometer)
                if self.nrm is not None:
                    self.known_msm_states[i] = self.kmeans_mdl.transform(
                                                            self.tica_mdl.transform(
                                                                [self.nrm.transform(
                                                                    self.featurizer.transform([self.top])[0])]
                                                            ))[0][0]
                else:
                    self.known_msm_states[i] = self.kmeans_mdl.transform(
                                                            self.tica_mdl.transform(
                                                                self.featurizer.transform([self.top])
                                                            ))[0][0]
                print(i, self.known_msm_states[i])
            if self.metad_sim.msm_swap_scheme=='wt_msm':
                self.wt_msm_mdl = self.metad_sim.wt_msm_mdl

        else:
            raise ValueError("MSM swap scheme is invalid")

        return

    def run(self):
        for step in range(self.metad_sim.n_iterations):
            # for eg 2fs *3000 = 6ps
            self.step = step
            self.sim_obj.step(self.metad_sim.swap_rate)
            current_sim_time = self.sim_obj.context.getState().getTime()
            if self.metad_sim.msm_swap_folder is not None:
                self.mix_with_msm()
            self.mix_all_replicas()
            comm.barrier()
            self.sim_obj.context.setTime(current_sim_time)
        if self.rank==0 and self.size >1:
            self.log_file.close()


    def get_energy(self):
        if self.metad_sim.neutral_replica and self.rank==self.size-1:
            return 0
        else:
            return self.sim_obj.context.getState(getEnergy=True,groups={self.force_group}).\
                getPotentialEnergy().value_in_unit(kilojoule_per_mole)

    def mix_all_replicas(self):
        old_energy = self.get_energy()
        #write the chckpt
        with open("checkpt.chk",'wb') as f:
            f.write(self.sim_obj.context.createCheckpoint())
        old_state = os.path.abspath("checkpt.chk")
        #send state and energy
        data = comm.gather((old_state,old_energy), root=0)
        if self.size >1:
            if self.rank==0:
                #rnd pick 2 states
                i,j =  np.random.choice(np.arange(self.size), 2, replace=False)
                s_i_i, e_i_i = data[i]
                s_j_j,e_j_j = data[j]
                #swap out states
                data[j], data[i] = data[i],data[j]
            else:
                data = None

            #get possible new state
            new_state = None
            new_state, energy = comm.scatter(data,root=0)
            #set state
            with open(new_state, 'rb') as f:
                self.sim_obj.context.loadCheckpoint(f.read())

            # return new state and new energies
            new_energy = self.get_energy()
            data = comm.gather((new_state,new_energy), root=0)

            if rank==0:
                s_i_j, e_i_j = data[i]
                s_j_i, e_j_i = data[j]
                delta_e = e_i_i+e_j_j - e_i_j - e_j_i

                #delta e is old_energy minus new energy
                #if new energy is higher than old energy,
                # delta_e is small-large < 0
                #if new energy is lower than older energy,
                # delta_e is large-small > 0, i.e. accept

                #if delta e >0, exp > 0 and prob = 1
                # else its a finite number
                probability = np.min((1, np.exp(self.beta*delta_e)))

                print(e_i_i,e_j_j,e_i_j,e_j_i,probability)
                # check if we are greater than random .
                # if probabilty is 0.05, this should fail about 95% of the time.
                #
                if probability >= np.random.random():
                    accepted= 1
                    print("Swapping out %d with %d"%(i,j),
                          flush=True)
                else:
                    accepted= 0
                    print("Failed Swap of %d with %d"%(i,j),
                          flush=True)
                    #go back to original state list
                    data[i], data[j] = data[j] , data[i]
                header = [self.step, i, j, e_i_i,e_j_j,e_i_j,e_j_i,delta_e,
                          self.metad_sim.temp,self.beta,probability,accepted]
                self.log_file.writelines("{}\t{}\t{}\t{}\t{}\t{}\t"
                                         "{}\t{}\t{}\t{}\t{}\t{}\n".format(*header))
                self.log_file.flush()
            else:
                data = None

            #get final state for iteration
            new_state,energy = comm.scatter(data,root=0)
            #print(rank,new_state)
            with open(new_state, 'rb') as f:
                self.sim_obj.context.loadCheckpoint(f.read())
        return

    def mix_with_msm(self):
        if self.metad_sim.neutral_replica and self.rank==self.size-1:
            return
        if self.metad_sim.msm_swap_scheme=='random':
            flist = self.full_list
        elif self.metad_sim.msm_swap_scheme == 'swap_once':
            flist = list(set(self.full_list).difference(set(self._tabu_list)))
        elif self.metad_sim.msm_swap_scheme in ['tabu_list',"min_count"]:
            current_traj = md.load("./trajectory.dcd", top=self.top)
            current_states = self.kmeans_mdl.transform(self.tica_mdl.transform(
                                                self.featurizer.transform([current_traj])))[0]

            if self.metad_sim.msm_swap_scheme == 'tabu_list':
                current_states =  np.unique(current_states)
                flist = [fname for fname in self.known_msm_states.keys()
                         if self.known_msm_states[fname]
                                   not in current_states]
            else:
                #count accessible states
                flist = []
                bin_counts = np.bincount(current_states,
                                         minlength=self.kmeans_mdl.n_clusters)
                bin_priority = np.argsort(bin_counts)
                for bin_index in bin_priority:
                    flist = [fname for fname in self.known_msm_states.keys()
                         if self.known_msm_states[fname]==bin_index]
                    if len(flist)>0:
                        break
        elif self.metad_sim.msm_swap_scheme == 'wt_msm':
            current_state = self.sim_obj.context.getState(getPositions=True)
            self.top.xyz = np.array(current_state.getPositions()/nanometer)

            if self.nrm is not None:
                self.msm_state = self.wt_msm_mdl.transform(self.kmeans_mdl.transform(
                                                            self.tica_mdl.transform(
                                                                [self.nrm.transform(
                                                                    self.featurizer.transform([self.top])[0])]
                                                            )))[0][0]
            else:
                self.msm_state = self.wt_msm_mdl.transform(self.kmeans_mdl.transform(
                                                            self.tica_mdl.transform(
                                                                self.featurizer.transform([self.top])
                                                            )))[0][0]
            #get states you are most likely to transition to
            next_likely_state = np.random.choice(range(self.wt_msm_mdl.n_states_),
                                                  size=1,
                                                  p=self.wt_msm_mdl.transmat_[self.msm_state,:])[0]
            flist = [fname for fname in self.known_msm_states.keys()
                     if self.wt_msm_mdl.transform([self.known_msm_states[fname]])[0] == next_likely_state]
            print(self.msm_state, next_likely_state, flist)

        else:
            raise ValueError("Sorry that MSM sampler is not implemented")
        if len(flist)==0 and self.metad_sim.msm_swap_scheme in ["swap_once", "tabu_list","wt_msm"]:
            print("Already done all possible MSM swaps or state not found. Returning")
            return
        print("Found %d states"%len(flist), flush=True)
        random_chck = np.random.choice(flist)
        print("Attempting swap with %s"%random_chck, flush=True)
        old_state=self.sim_obj.context.getState(getPositions=True, getVelocities=True,\
        getForces=True,getEnergy=True,getParameters=True,enforcePeriodicBox=True)

        old_energy = self.sim_obj.context.getState(getEnergy=True,groups={self.force_group}).\
            getPotentialEnergy().value_in_unit(kilojoule_per_mole)

        new_state = XmlSerializer.deserialize(open(random_chck).read())
        self.sim_obj.context.setState(new_state)
        new_energy = self.sim_obj.context.getState(getEnergy=True,groups={self.force_group}).\
                getPotentialEnergy().value_in_unit(kilojoule_per_mole)
        #if new_e < old_e , delta e is >0 and p ==1
        delta_e = old_energy - new_energy
        probability = np.min((1,np.exp(self.beta*delta_e)))
        accept = np.random.random() < probability
        if accept:
            print("Swap accepted with %s"%random_chck)
            if self.metad_sim.msm_swap_scheme == 'swap_once':
                self._tabu_list.append(random_chck)
        else:
            #reset back to old_state
            self.sim_obj.context.setState(old_state)
        return


def run_meta_sim(file_loc="metad_sim.pkl"):
    from tica_metadynamics.load_sim import create_simulation

    metad_sim = load(file_loc)
    if metad_sim.msm_swap_folder is not None:
        print("Found MSM state folder. Will swap all replicas with the MSM "
              "occasionally",flush=True)
    #beta is 1/kt
    beta = 1/(boltzmann_constant * metad_sim.temp)

    #get
    my_host_name = socket.gethostname()
    my_gpu_index = get_gpu_index()
    print("Hello from rank %d running tic %d on "
          "host %s with gpu %d"%(rank, rank, my_host_name, my_gpu_index))

    plumed_force_dict = get_plumed_dict(metad_sim)
    sim_obj, force_group = create_simulation(metad_sim.base_dir, metad_sim.starting_coordinates_folder,
                                my_gpu_index, rank, plumed_force_dict[rank],
                                metad_sim.sim_save_rate, metad_sim.platform)
    if rank ==0 and size>1:
        log_file = open("../swap_log.txt","a")
        header = ["Iteration","S_i","S_j","Eii","Ejj","Eij","Eji",
                  "DeltaE","Temp","Beta","Probability","Accepted"]
        log_file.writelines("#{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(*header))

    for step in range(metad_sim.n_iterations):
        #2fs *3000 = 6ps
        sim_obj.step(metad_sim.swap_rate)

        if metad_sim.msm_swap_folder is not None and np.random.random() < 0.5:
            sim_obj = swap_with_msm_state(sim_obj, metad_sim.msm_swap_folder, force_group,beta)
        #get old energy for just the plumed force
        old_energy = sim_obj.context.getState(getEnergy=True,groups={force_group}).\
            getPotentialEnergy().value_in_unit(kilojoule_per_mole)
        #write the chckpt
        with open("checkpt.chk",'wb') as f:
            f.write(sim_obj.context.createCheckpoint())
        old_state = os.path.abspath("checkpt.chk")
        #send state and energy
        data = comm.gather((old_state,old_energy), root=0)
        if size >1:
            if rank==0:
                #rnd pick 2 states
                i,j =  np.random.choice(np.arange(size), 2, replace=False)
                s_i_i,e_i_i = data[i]
                s_j_j,e_j_j = data[j]
                #swap out states
                data[j], data[i] = data[i],data[j]
            else:
                data = None

            #get possible new state
            new_state = None
            new_state,energy = comm.scatter(data,root=0)
            #set state
            with open(new_state, 'rb') as f:
                sim_obj.context.loadCheckpoint(f.read())

            # return new state and new energies
            new_energy = sim_obj.context.getState(getEnergy=True,groups={force_group}).\
                getPotentialEnergy().value_in_unit(kilojoule_per_mole)
            data = comm.gather((new_state,new_energy), root=0)

            if rank==0:
                s_i_j, e_i_j = data[i]
                s_j_i, e_j_i = data[j]
                delta_e = e_i_i+e_j_j - e_i_j - e_j_i
                probability = np.min((1,np.exp(beta*delta_e)))
                print(e_i_i,e_j_j,e_i_j,e_j_i,probability)
                if np.random.random() < probability :
                    accepted= 1
                    print("Swapping out %d with %d"%(i,j),flush=True)
                else:
                    accepted= 0
                    print("Failed Swap of %d with %d"%(i,j),flush=True)
                    #go back to original state list
                    data[i], data[j] = data[j] , data[i]
                header = [step, i, j, e_i_i,e_j_j,e_i_j,e_j_i,delta_e,metad_sim.temp,beta,probability,accepted]
                log_file.writelines("{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\t{}\n".format(*header))
                log_file.flush()
            else:
                data = None

            #get final state for iteration
            new_state,energy = comm.scatter(data,root=0)
            #print(rank,new_state)
            with open(new_state, 'rb') as f:
                sim_obj.context.loadCheckpoint(f.read())
            #barrier here to prevent
            comm.barrier()

    if rank==0 and size >1:
        log_file.close()
    return

def parse_commandline():
    parser = argparse.ArgumentParser()
    parser.add_argument('-f','--file', dest='f',
                            default='./metad_sim.pkl',
              help='TICA METAD location file')
    args = parser.parse_args()
    return args

def main():
    args = parse_commandline()
    file_loc = args.f
    sim_obj = TicaSimulator(file_loc)
    sim_obj.run()
    return

if __name__ == "__main__":
    main()
