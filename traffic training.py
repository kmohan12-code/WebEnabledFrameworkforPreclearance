import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import random
from collections import deque

class QNetwork(nn.Module):
    def __init__(self, state_size, action_size):
        super(QNetwork, self).__init__()
        self.fc1 = nn.Linear(state_size, 128)
        self.fc2 = nn.Linear(128, 128)
        self.fc3 = nn.Linear(128, action_size)

    def forward(self, x):
        x = torch.relu(self.fc1(x))
        x = torch.relu(self.fc2(x))
        return self.fc3(x)

class DQNAgent:
    def __init__(self, state_size, action_size):
        self.state_size = state_size
        self.action_size = action_size
        self.memory = deque(maxlen=5000)
        self.gamma = 0.95
        self.epsilon = 1.0
        self.epsilon_min = 0.01
        self.epsilon_decay = 0.995
        self.model = QNetwork(state_size, action_size)
        self.target_model = QNetwork(state_size, action_size)
        self.update_target()
        self.optimizer = optim.Adam(self.model.parameters(), lr=0.001)
        self.loss_fn = nn.MSELoss()

    def update_target(self):
        self.target_model.load_state_dict(self.model.state_dict())

    def act(self, state):
        if np.random.rand() <= self.epsilon:
            return random.randrange(self.action_size)
        state = torch.FloatTensor(state)
        q_values = self.model(state)
        return torch.argmax(q_values).item()

    def remember(self, state, action, reward, next_state, done):
        self.memory.append((state, action, reward, next_state, done))

    def replay(self, batch_size):
        if len(self.memory) < batch_size:
            return
        minibatch = random.sample(self.memory, batch_size)
        for state, action, reward, next_state, done in minibatch:
            state_t = torch.FloatTensor(state)
            next_state_t = torch.FloatTensor(next_state)
            target = self.model(state_t)[action]
            if done:
                target_val = torch.tensor(reward)
            else:
                target_val = reward + self.gamma * torch.max(self.target_model(next_state_t)).item()
                target_val = torch.tensor(target_val)
            loss = self.loss_fn(target, target_val)
            self.optimizer.zero_grad()
            loss.backward()
            self.optimizer.step()
        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay

class TrafficEnv:
    def __init__(self):
        self.state_size = 5
        self.action_size = 3
        self.reset()

    def reset(self):
        self.state = np.random.rand(self.state_size)
        return self.state

    def step(self, action):
        next_state = np.random.rand(self.state_size)
        reward = np.random.rand() - 0.5
        done = np.random.rand() > 0.95
        return next_state, reward, done, {}

env = TrafficEnv()
state_size = env.state_size
action_size = env.action_size
agent = DQNAgent(state_size, action_size)
episodes = 300
batch_size = 32

for e in range(episodes):
    state = env.reset()
    total_reward = 0
    for time in range(200):
        action = agent.act(state)
        next_state, reward, done, _ = env.step(action)
        agent.remember(state, action, reward, next_state, done)
        state = next_state
        total_reward += reward
        agent.replay(batch_size)
        if done:
            break
    agent.update_target()