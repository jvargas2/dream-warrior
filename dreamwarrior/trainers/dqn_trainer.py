import argparse
import logging
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
from dreamwarrior.agents import DQNAgent
from dreamwarrior.memory import ReplayMemory

Transition = namedtuple('Transition', ('state', 'action', 'next_state', 'reward'))

BATCH_SIZE = 32
GAMMA = 0.999
EPS_START = 0.9
EPS_END = 0.05
EPS_DECAY = 200
NUM_EPISODES = 100
FRAME_SKIP = 4

class DQNTrainer():
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    env = None
    agent = None

    def __init__(self, env):
        self.env = env
        n_actions = env.action_space.n

        init_screen = self.get_screen()
        _, _, screen_height, screen_width = init_screen.shape

        self.agent = DQNAgent(env, screen_height, screen_width)

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

    def training_select_action(self, state, steps_done):
        # Select and perform an action
        sample = random.random()
        eps_threshold = EPS_END + (EPS_START - EPS_END) * \
            math.exp(-1. * steps_done / EPS_DECAY)
        steps_done += 1
        
        if sample > eps_threshold:
            action = self.agent.select_action(state)
        else:
            action = self.agent.random_action()

        return action

    def train(self, optimizer_state=None, start_episode=0, watching=False):
        logging.info('Starting training...')
        env = self.env
        device = self.device

        # Get screen size so that we can initialize layers correctly based on shape
        # returned from AI gym. Typical dimensions at this point are close to ???
        # which is the result of a clamped and down-scaled render buffer in get_screen()
        init_screen = self.get_screen()
        _, _, screen_height, screen_width = init_screen.shape

        # Get number of actions from gym action space
        self.n_actions = env.action_space.n
        
        # TODO Add functions to DQNAgent for the following
        self.agent.target_net.load_state_dict(self.agent.policy_net.state_dict())
        self.agent.target_net.eval()
        optimizer = optim.RMSprop(self.agent.policy_net.parameters())

        if optimizer_state:
            optimizer.load_state_dict(optimizer_state)

        memory = ReplayMemory(10000)

        steps_done = 0
        episode_rewards = []

        for i_episode in range(start_episode, NUM_EPISODES):
            # Initialize the environment and state
            env.reset()
            episode_reward = 0
            last_screen = self.get_screen()
            current_screen = self.get_screen()
            state = current_screen - last_screen
            previous_action = self.training_select_action(state, steps_done)

            for t in count():
                if t % FRAME_SKIP == 0:
                    action = self.training_select_action(state, steps_done)
                    previous_action = action
                else:
                    action = previous_action

                retro_action = np.zeros((9,), dtype=int)
                retro_action[action.item()] = 1
                _, reward, done, _ = env.step(retro_action)
                episode_reward += reward

                if reward > 0:
                    logging.info('t=%i got reward: %g' % (t, reward))
                elif reward < 0:
                    logging.info('t=%i got penalty: %g' % (t, reward))

                reward = torch.tensor([reward], device=device)
                
                if watching:
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
                loss = self.agent.optimize_model(optimizer, memory, BATCH_SIZE, GAMMA)

                if t % 1000 == 0 and len(memory) >= BATCH_SIZE:
                    logging.info('t=%d loss: %f' % (t, loss))

                # if t > 5:
                #     done = True

                if done:
                    break
            # Update the target network, copying all weights and biases in DQN
            # if i_episode % TARGET_UPDATE == 0:
            self.target_net.load_state_dict(self.policy_net.state_dict())

            logging.info('Finished episode ' + str(i_episode))
            logging.info('Final reward: %d' % episode_reward)
            episode_rewards.append(episode_reward)
            self.training_save(i_episode, optimizer)

        env.close()
        logging.info('Finished training! Final rewards per episode:')
        logging.info(episode_rewards)

    # TODO Needs to be moved to new file
    def run(self):
        env = self.env
        device = self.device

        # Initialize the environment and state
        env.reset()
        last_screen = self.get_screen()
        current_screen = self.get_screen()
        state = current_screen - last_screen
        final_reward = 0

        for t in count():
            action = self.select_action(state)

            retro_action = np.zeros((9,), dtype=int)
            retro_action[action.item()] = 1
            _, reward, done, _ = env.step(retro_action)
            final_reward += reward
            reward = torch.tensor([reward], device=device)

            if reward > 0:
                print('t=%i got reward: %g' % (t, reward))

            env.render()

            # Observe new state
            last_screen = current_screen
            current_screen = self.get_screen()
            state = current_screen - last_screen

            if done:
                break

        env.close()
        logging.info('Finished run.')
        logging.info('Final reward: %d' % final_reward)

env = dreamwarrior.make_custom_env('NightmareOnElmStreet-Nes', record=True)
trainer = DQNTrainer(env)
trainer.train(watching=True)