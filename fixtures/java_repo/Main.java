import com.example.shop.cart.Cart;
import com.example.shop.store.Store;

public class Main {
    public static void main(String[] args) {
        Cart cart = new Cart(new Store());
        cart.add(args[0]);
        System.out.println(cart.describe());
    }
}
