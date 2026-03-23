import traci
 
 
class SumoEnvironment:
    def __init__(self, sumo_cfg_file):
        self.sumo_cfg = sumo_cfg_file
        self.state_size = 6   # 6 lanes
        self.action_size = 6  # 6 phases
        self.connected = False
 
    def reset(self):
        if self.connected:
            traci.close()
            self.connected = False
 
        traci.start(['sumo', '-c', self.sumo_cfg])
        self.connected = True
        return self.get_state()
 
    def step(self, action):
        self.apply_action(action)
        traci.simulationStep()
        next_state = self.get_state()
        reward = self.get_reward()
        done = self.is_done()
        return next_state, reward, done
 
    def get_state(self):
        lane_ids = traci.trafficlight.getControlledLanes("11306430808")
        return [traci.lane.getWaitingTime(lane) for lane in lane_ids]
 
    def apply_action(self, action):
        traci.trafficlight.setPhase("11306430808", action)
 
    def get_reward(self):
        lane_ids = traci.trafficlight.getControlledLanes("11306430808")
        return -sum(traci.lane.getWaitingTime(lane) for lane in lane_ids)
 
    def is_done(self):
        return traci.simulation.getMinExpectedNumber() <= 0
 
    def close(self):
        if self.connected:
            traci.close()
            self.connected = False