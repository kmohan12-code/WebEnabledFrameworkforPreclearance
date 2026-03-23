from environment import SumoEnvironment
from dqn_agent import Agent

SUMO_CFG = "multi_intersection.sumocfg"


env = SumoEnvironment(SUMO_CFG)
state_size = env.state_size
action_size = env.action_size
agent = Agent(state_size, action_size)

episodes = 100  
batch_size = 32

for e in range(episodes):
    state = env.reset()
    done = False
    total_reward = 0
    while not done:
        action = agent.act(state)
        next_state, reward, done = env.step(action)
        agent.remember(state, action, reward, next_state, done)
        state = next_state
        total_reward += reward
    agent.replay(batch_size)
    print(f"Episode {e+1}/{episodes}, Total Reward: {total_reward:.2f}, Epsilon: {agent.epsilon:.2f}")

env.close()
