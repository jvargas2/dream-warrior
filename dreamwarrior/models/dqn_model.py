import math
import random
import numpy as np
from collections import namedtuple
from itertools import count
from PIL import Image

import torch
import torch.nn.functional as F
import torch.optim as optim
from torchvision import transforms

import dreamwarrior
from dreamwarrior.networks import DQN
from dreamwarrior.utils import ReplayMemory

Transition = namedtuple('Transition',
                        ('state', 'action', 'next_state', 'reward'))

class DQN_Model():
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    env = None
    policy_net = None
    target_net = None

    def __init__(self, env):
        self.env = env

    def get_screen(self):
        """Get retro env render as a torch tensor.

        Returns: A torch tensor made from the RGB pixels
        """
        env = self.env

        # Transpose it into torch order (CHW).
        screen = env.render(mode='rgb_array').transpose((2, 0, 1))

        # Convert to float, rescale, convert to torch tensor
        # (this doesn't require a copy)
        screen = np.ascontiguousarray(screen, dtype=np.float32) / 255
        screen = torch.from_numpy(screen)

        # Resize, and add a batch dimension (BCHW)
        transforms.Compose([
            screen,
            transforms.ToPILImage(),
            transforms.Resize(40, interpolation=Image.CUBIC),
            transforms.ToTensor()
        ])

        return screen.unsqueeze(0).to(self.device)

    def optimize_model(self, optimizer, memory, batch_size, gamma):
        if len(memory) < batch_size:
            return
        transitions = memory.sample(batch_size)
        # Transpose the batch (see https://stackoverflow.com/a/19343/3343043 for
        # detailed explanation). This converts batch-array of Transitions
        # to Transition of batch-arrays.
        batch = Transition(*zip(*transitions))

        # Compute a mask of non-final states and concatenate the batch elements
        # (a final state would've been the one after which simulation ended)
        non_final_mask = torch.tensor(tuple(map(lambda s: s is not None,
                                            batch.next_state)), device=self.device, dtype=torch.uint8)
        non_final_next_states = torch.cat([s for s in batch.next_state
                                                    if s is not None])
        state_batch = torch.cat(batch.state)
        action_batch = torch.cat(batch.action)
        reward_batch = torch.cat(batch.reward)

        # Compute Q(s_t, a) - the model computes Q(s_t), then we select the
        # columns of actions taken. These are the actions which would've been taken
        # for each batch state according to policy_net
        state_action_values = self.policy_net(state_batch).gather(1, action_batch)

        # Compute V(s_{t+1}) for all next states.
        # Expected values of actions for non_final_next_states are computed based
        # on the "older" target_net; selecting their best reward with max(1)[0].
        # This is merged based on the mask, such that we'll have either the expected
        # state value or 0 in case the state was final.
        next_state_values = torch.zeros(batch_size, device=self.device)
        next_state_values[non_final_mask] = self.target_net(non_final_next_states).max(1)[0].detach()
        # Compute the expected Q values
        expected_state_action_values = (next_state_values * gamma) + reward_batch

        # Compute Huber loss
        loss = F.smooth_l1_loss(state_action_values, expected_state_action_values.unsqueeze(1))

        # Optimize the model
        optimizer.zero_grad()
        loss.backward()
        for param in self.policy_net.parameters():
            param.grad.data.clamp_(-1, 1)
        optimizer.step()

    def train(self):
        batch_size = 128
        gamma = 0.999
        eps_start = 0.9
        eps_end = 0.05
        eps_decay = 200
        target_update = 10

        env = self.env
        device = self.device

        env.reset()

        # Get screen size so that we can initialize layers correctly based on shape
        # returned from AI gym. Typical dimensions at this point are close to ???
        # which is the result of a clamped and down-scaled render buffer in get_screen()
        init_screen = self.get_screen()
        _, _, screen_height, screen_width = init_screen.shape

        # Get number of actions from gym action space
        n_actions = env.action_space.n

        self.policy_net = DQN(screen_height, screen_width, n_actions).to(device)
        self.target_net = DQN(screen_height, screen_width, n_actions).to(device)

        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.target_net.eval()

        optimizer = optim.RMSprop(self.policy_net.parameters())
        memory = ReplayMemory(10000)

        steps_done = 0

        episode_durations = []

        num_episodes = 50
        for i_episode in range(num_episodes):
            # Initialize the environment and state
            env.reset()
            last_screen = self.get_screen()
            current_screen = self.get_screen()
            state = current_screen - last_screen
            for t in count():
                # Select and perform an action
                sample = random.random()
                eps_threshold = eps_end + (eps_start - eps_end) * \
                    math.exp(-1. * steps_done / eps_decay)
                steps_done += 1
                if sample > eps_threshold:
                    with torch.no_grad():
                        # t.max(1) will return largest column value of each row.
                        # second column on max result is index of where max element was
                        # found, so we pick action with the larger expected reward.
                        action = self.policy_net(state).max(1)[1].view(1, 1)
                else:
                    action = torch.tensor([[random.randrange(n_actions)]], device=device, dtype=torch.long)

                retro_action = np.zeros((9,), dtype=int)
                retro_action[action.item()] = 1
                _, reward, done, _ = env.step(retro_action)
                reward = torch.tensor([reward], device=device)

                if t % 10:
                    env.render()

                # Observe new state
                last_screen = current_screen
                current_screen = self.get_screen()
                if not done:
                    next_state = current_screen - last_screen
                else:
                    next_state = None

                # Store the transition in memory
                memory.push(state, action, next_state, reward)

                # Move to the next state
                state = next_state

                # Perform one step of the optimization (on the target network)
                self.optimize_model(optimizer, memory, batch_size, gamma)

                if done:
                    episode_durations.append(t + 1)
                    break
            # Update the target network, copying all weights and biases in DQN
            if i_episode % target_update == 0:
                self.target_net.load_state_dict(self.policy_net.state_dict())

            print('Finished episode loop')

        env.close()
        print('Reached end of train function')