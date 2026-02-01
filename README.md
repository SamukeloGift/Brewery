# Brewery Package
This is a home brew wrapper, a side project i have been designing while exploring Package Management on MacOs. The end Goal was to design it in Rust just to see the difference in performance, and also to have such a reputable project under my name too!. But, i have been battling the rust's ownership model when parsing strings into functions and having to obey Rust's principles of programming. So i thought, maybe if i can build a prototype in Python first, i can then port it to Rust with more ease.

The project is just a wrapper i wanted to build for myself as i do not like dealing with BlackBoxes. So this is just a simple CLI that wraps around Homebrew command lINE and just does basic stuff like `installing`, `uninstalling`, `updating`, `list` and `searching` for packages. It doesn't have the fancy algorithms to resolve dependencies, for now, I am more about tailoring it for my needs and having to upgrade it as my needs change. If you happen to want to test this product, You can simple git clone the repo? or if you want to test it on your machine(no sudo rights for now), You can run the following shell script which will pull and set up your environment to use Brewery.

```sh
curl -fsSL https://raw.githubusercontent.com/SamukeloGift/Brewery/setup.sh | bash
```
this works natively on MacOS and Linux machines with `curl` installed. But if you are using Windows, you can just clone the repo and run the setup script manually.

You can drop a feedback on your experience using the `Issues` tab on the repo. I would love to hear from you.
